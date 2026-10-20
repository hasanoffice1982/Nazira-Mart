"""Send dealer items of a confirmed order to the dealer's automation, and take tracking back."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.services import OrderError
from apps.supplier.services import advance, map_status

from .models import Dealer, DealerOrder

logger = logging.getLogger("apps.dealers")
S = Order.Status
TIMEOUT = 15


def dealer_items(order: Order) -> dict[int, list]:
    """Order items grouped by the dealer who ships them (items of dropship or own-stock products are left out)."""
    groups: dict[int, list] = {}
    for item in order.items.select_related("product", "product__dealer"):
        product = item.product
        if product is not None and product.dealer_id and product.dealer.is_active:
            groups.setdefault(product.dealer_id, []).append(item)
    return groups


def build_payload(order: Order, dealer: Dealer, items: list, reference: str, event: str = "order.confirmed") -> dict:
    return {
        "event": event,
        "reference": reference,   # send this back with the tracking
        "order_number": order.number,
        "placed_at": order.created_at.isoformat(),
        "dealer": {"id": dealer.pk, "name": dealer.name, "phone": dealer.phone, "email": dealer.email},
        "customer": {"name": order.full_name, "phone": order.phone, "address": order.address, "city": order.city, "delivery_area": order.delivery_area},
        "cod_amount": str(order.cod_amount),   # cash the courier must collect (the whole order, less a delivery charge paid in advance)
        "delivery_charge": str(order.delivery_charge),
        "delivery_prepaid": order.delivery_prepaid,
        "items": [
            {"sku": i.sku, "name": i.name, "variant": i.variant_label, "quantity": i.quantity, "unit_price": str(i.unit_price)}
            for i in items
        ],
        "callback_url": dealer.callback_url,
    }


def _post(dealer: Dealer, payload: dict) -> None:
    url = dealer.webhook_url
    if not url.startswith("https://") and not (settings.DEBUG and url.startswith("http://")):
        raise ValueError("The webhook address must start with https://")
    body = json.dumps(payload).encode()
    signature = hmac.new(dealer.secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "NarizMart/1.0", "X-NarizMart-Signature": f"sha256={signature}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:   # noqa: S310  https only, address entered by staff
        resp.read(2048)


def send_order(order: Order, event: str = "order.confirmed") -> list[DealerOrder]:
    """Send every dealer's part of the order. Safe to call again: parts already sent are skipped, failed ones retried."""
    results = []
    for dealer_id, items in dealer_items(order).items():
        dealer = items[0].product.dealer
        reference = f"{order.number}-{dealer_id}"
        part, _ = DealerOrder.objects.get_or_create(order=order, dealer=dealer, defaults={"reference": reference})
        results.append(part)
        if event == "order.confirmed" and part.state == DealerOrder.State.SENT:
            continue
        if not dealer.webhook_url:
            part.state, part.error = DealerOrder.State.MANUAL, "No automation webhook set for this dealer: order them by hand."
            part.save()
            continue
        try:
            _post(dealer, build_payload(order, dealer, items, part.reference, event))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("dealer %s: sending %s failed: %s", dealer.name, order.number, exc)
            if event == "order.confirmed":
                part.state, part.error = DealerOrder.State.FAILED, str(exc)[:300]
                part.save()
        else:
            if event == "order.confirmed":
                part.state, part.error, part.sent_at = DealerOrder.State.SENT, "", timezone.now()
                part.save()
    return results


def on_status_change(order_id: int, new_status: str) -> None:
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        return
    if new_status == S.CONFIRMED:
        send_order(order)
    elif new_status == S.CANCELLED and order.dealer_orders.filter(state=DealerOrder.State.SENT).exists():
        send_order(order, event="order.cancelled")


def retry_failed(days: int = 3) -> int:
    since = timezone.now() - timedelta(days=days)
    orders = Order.objects.filter(status__in=(S.CONFIRMED, S.PROCESSING), created_at__gte=since, dealer_orders__state=DealerOrder.State.FAILED).distinct()
    return sum(1 for o in orders for p in send_order(o) if p.state == DealerOrder.State.SENT)


@transaction.atomic
def apply_update(dealer: Dealer, payload: dict) -> bool:
    """Tracking and status from the dealer's automation. Needs `reference` (best) or `order_number`."""
    reference = str(payload.get("reference") or "").strip()
    qs = DealerOrder.objects.select_related("order").filter(dealer=dealer)
    part = qs.filter(reference=reference).first() if reference else qs.filter(order__number=str(payload.get("order_number") or "").strip()).first()
    if part is None:
        return False
    if payload.get("status"):
        part.dealer_status = str(payload["status"])[:40]
    for field, size in (("courier_name", 60), ("tracking_code", 80), ("tracking_link", 300)):
        if payload.get(field):
            setattr(part, field, str(payload[field])[:size])
    part.save()
    order = Order.objects.select_for_update().get(pk=part.order_id)
    if part.tracking_code and not order.tracking_number:
        order.tracking_number = part.tracking_code
        order.save(update_fields=["tracking_number", "updated_at"])
    target = map_status(part.dealer_status, order.status)
    if target:
        try:
            advance(order, target, note=f"Dealer {dealer.name}: {part.dealer_status}" + (f" ({part.courier_name})" if part.courier_name else ""))
        except OrderError:
            logger.exception("dealer update could not move order %s to %s", order.number, target)
    return True
