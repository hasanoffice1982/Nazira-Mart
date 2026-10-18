"""Send confirmed orders to the supplier and apply the status updates they send back."""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.services import TRANSITIONS, OrderError, change_status

from . import client
from .models import SupplierOrder

logger = logging.getLogger("apps.supplier")
S = Order.Status
CHAIN = [S.PENDING, S.CONFIRMED, S.PROCESSING, S.SHIPPED, S.DELIVERED]
ZONES = {"dhaka": "Inside Dhaka", "sub": "Dhaka Sub Area", "outside": "Outside Dhaka"}   # exact strings the supplier API expects


def postal_code(order: Order) -> str:
    found = re.search(r"\b(\d{4})\b", f"{order.address} {order.city}")
    return found.group(1) if found else ("1000" if order.delivery_area in ("dhaka", "sub") else "0000")


def build_payload(order: Order, partner_id: str, supplier_product_id: str, quantity: int) -> dict:
    payload = {
        "partner_order_id": partner_id, "product_id": supplier_product_id,
        "customer_name": order.full_name, "customer_phone": order.phone,
        "customer_address": f"{order.address}, {order.city}"[:255], "postal_code": postal_code(order),
        "city_or_zone": ZONES.get(order.delivery_area, "Outside Dhaka"), "payment_method": "Cash on Delivery",
    }
    if quantity > 1 and settings.SUPPLIER_SEND_QUANTITY:
        payload["quantity"] = quantity
    return payload   # never a price: the supplier uses its own


def send_order(order: Order) -> list[SupplierOrder]:
    """Send every supplier item of the order. Safe to call again: parcels already sent are skipped, failed ones retried."""
    items = [i for i in order.items.select_related("product__supplier_link") if i.product_id and hasattr(i.product, "supplier_link")]
    results: list[SupplierOrder] = []
    for n, item in enumerate(items, 1):
        partner_id = order.number if len(items) == 1 else f"{order.number}-{n}"
        parcel, _ = SupplierOrder.objects.get_or_create(partner_order_id=partner_id, defaults={"order": order, "item": item})
        if parcel.state == SupplierOrder.State.SENT:
            results.append(parcel)
            continue
        if item.quantity > 1 and not settings.SUPPLIER_SEND_QUANTITY:
            parcel.state, parcel.error = SupplierOrder.State.MANUAL, f"Quantity is {item.quantity}: place this order by hand in the supplier panel."
            parcel.save()
            results.append(parcel)
            continue
        try:
            reply = client.place_order(build_payload(order, partner_id, item.product.supplier_link.supplier_product_id, item.quantity))
        except client.SupplierError as exc:
            parcel.state, parcel.error = SupplierOrder.State.FAILED, str(exc)[:300]
        else:
            details = reply.get("order_details", {})
            parcel.state, parcel.error = SupplierOrder.State.SENT, ""
            parcel.supplier_order_id = str(details.get("system_order_id", ""))
            parcel.supplier_status = str(details.get("status", ""))
            parcel.sent_at = timezone.now()
        parcel.save()
        results.append(parcel)
    return results


def has_supplier_items(order: Order) -> bool:
    return any(i.product_id and hasattr(i.product, "supplier_link") for i in order.items.select_related("product__supplier_link"))


def risky_phone(order: Order) -> bool:
    """A number that already refused or cancelled an order is never auto-confirmed (staff call first)."""
    return Order.objects.filter(phone=order.phone, status__in=Order.INACTIVE_STATUSES).exclude(pk=order.pk).exists()


def maybe_auto_confirm(order_id: int) -> bool:
    """Confirm a brand-new order by itself when it is clearly safe; otherwise leave it for a phone call."""
    if not (settings.SUPPLIER_AUTO_CONFIRM and client.configured()):
        return False
    order = Order.objects.filter(pk=order_id, status=S.PENDING).first()
    if order is None or order.total > settings.SUPPLIER_AUTO_CONFIRM_MAX_TOTAL:
        return False
    items = list(order.items.select_related("product__supplier_link"))
    if not items or not all(i.product_id and hasattr(i.product, "supplier_link") and i.quantity == 1 for i in items) or risky_phone(order):
        return False
    change_status(order, S.CONFIRMED, note="Auto-confirmed (small order, new customer number, all items from the supplier)")
    return True


def cancel_at_supplier(order: Order) -> None:
    """Customer or staff cancelled: tell the supplier too, as long as they have not started fulfilment."""
    for parcel in order.supplier_orders.filter(state=SupplierOrder.State.SENT).exclude(supplier_order_id=""):
        try:
            client.set_status(parcel.supplier_order_id, "Canceled")
            parcel.supplier_status = "Canceled"
            parcel.save(update_fields=["supplier_status", "updated_at"])
        except client.SupplierError as exc:
            parcel.error = f"Cancel at supplier failed: {exc}"[:300]
            parcel.save(update_fields=["error", "updated_at"])


def on_status_change(order_id: int, new_status: str) -> None:
    """Called after a status change commits: confirmed orders go to the supplier, cancelled ones are cancelled there too."""
    if not client.configured():
        return
    order = Order.objects.filter(pk=order_id).first()
    if order is None:
        return
    if new_status == S.CONFIRMED and settings.SUPPLIER_AUTO_SEND:
        send_order(order)
    elif new_status == S.CANCELLED and settings.SUPPLIER_AUTO_SEND:
        cancel_at_supplier(order)


def retry_failed(days: int = 3) -> int:
    """Re-send parcels that failed (supplier down, wrong IP, empty wallet) for orders still waiting."""
    since = timezone.now() - timedelta(days=days)
    orders = Order.objects.filter(status__in=(S.CONFIRMED, S.PROCESSING), created_at__gte=since, supplier_orders__state=SupplierOrder.State.FAILED).distinct()
    sent = 0
    for order in orders:
        sent += sum(1 for p in send_order(order) if p.state == SupplierOrder.State.SENT)
    return sent


# --- supplier -> shop ---------------------------------------------------------------
def map_status(text: str, current: str) -> str | None:
    t = (text or "").strip().lower()
    started = current in (S.SHIPPED, S.DELIVERED)
    if t in ("processing", "packing", "approved"):
        return S.PROCESSING
    if t in ("shipped", "in transit", "out for delivery", "dispatched", "on the way"):
        return S.SHIPPED
    if t in ("delivered", "completed"):
        return S.DELIVERED
    if t in ("returned", "return", "rto", "refused", "returning"):
        return S.RETURNED if started else S.CANCELLED
    if t in ("canceled", "cancelled", "failed"):
        return S.RETURNED if started else S.CANCELLED
    return None   # New / Pending: nothing to do


def advance(order: Order, target: str, note: str) -> None:
    """Walk the order forward step by step to `target` (the shop's own rules still apply)."""
    if target in (S.CANCELLED, S.RETURNED):
        if order.status in TRANSITIONS and target in TRANSITIONS[order.status]:
            change_status(order, target, note=note)
        return
    if target not in CHAIN or order.status not in CHAIN:
        return
    while CHAIN.index(order.status) < CHAIN.index(target):
        change_status(order, CHAIN[CHAIN.index(order.status) + 1], note=note)
        order.refresh_from_db()


@transaction.atomic
def apply_webhook(payload: dict) -> bool:
    if payload.get("event") != "order.status_updated":
        return False
    parcel = SupplierOrder.objects.select_related("order").filter(partner_order_id=str(payload.get("partner_order_id", ""))).first()
    if parcel is None:
        return False
    parcel.supplier_status = str(payload.get("new_status", ""))[:40]
    for field, key, size in (("courier_name", "courier_name", 60), ("tracking_code", "tracking_code", 80), ("tracking_link", "tracking_link", 300)):
        if payload.get(key):
            setattr(parcel, field, str(payload[key])[:size])
    parcel.save()
    order = Order.objects.select_for_update().get(pk=parcel.order_id)
    if parcel.tracking_code and not order.tracking_number:
        order.tracking_number = parcel.tracking_code
        order.save(update_fields=["tracking_number", "updated_at"])
    target = map_status(parcel.supplier_status, order.status)
    if target:
        try:
            advance(order, target, note=f"Supplier: {parcel.supplier_status}" + (f" ({parcel.courier_name})" if parcel.courier_name else ""))
        except OrderError:
            logger.exception("supplier webhook could not move order %s to %s", order.number, target)
    return True
