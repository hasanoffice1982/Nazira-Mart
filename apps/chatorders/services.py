"""Chat orders: a bot (n8n + OpenAI) looks products up, quotes a total and creates a PENDING order. Staff confirm by phone as usual."""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import DeliveryArea, User
from apps.accounts.validators import clean_bd_phone
from apps.catalog.models import Product, ProductVariant
from apps.orders.models import Order
from apps.orders.services import OrderError, create_order

from .models import ChatOrder

PLACEHOLDER_DOMAIN = "chat.narizmart.invalid"   # these customers have no real email: no emails are sent to them
MAX_ITEMS, MAX_QTY, MAX_ORDERS_PER_PHONE_PER_DAY = 8, 5, 5


class ChatOrderError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class _Rollback(Exception):
    """Used to price an order for a quote and then throw it away."""


def chat_user_for(phone: str) -> User:
    email = f"{phone}@{PLACEHOLDER_DOMAIN}"
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User(email=email, phone=phone, email_verified=True, is_active=True)
        user.set_unusable_password()
        user.save()
    return user


def resolve_lines(items) -> list[tuple[int, int, int]]:
    if not isinstance(items, list) or not items:
        raise ChatOrderError("items must be a non-empty list of {sku, quantity}")
    if len(items) > MAX_ITEMS:
        raise ChatOrderError(f"At most {MAX_ITEMS} different items per order")
    lines = []
    for item in items:
        sku = str((item or {}).get("sku", "")).strip()
        try:
            qty = int((item or {}).get("quantity", 1))
        except (TypeError, ValueError):
            raise ChatOrderError(f"Bad quantity for {sku}") from None
        if not sku or not 1 <= qty <= MAX_QTY:
            raise ChatOrderError(f"Each item needs a sku and a quantity of 1 to {MAX_QTY}")
        variant = ProductVariant.objects.select_related("product").filter(sku__iexact=sku, is_active=True, product__is_active=True).first()
        if variant is not None:
            lines.append((variant.product_id, variant.pk, qty))
            continue
        product = Product.objects.filter(sku__iexact=sku, is_active=True).first()
        if product is None:
            raise ChatOrderError(f"Unknown sku {sku}")
        lines.append((product.pk, 0, qty))   # create_order asks for an option if the product has variants
    return lines


def summary(order: Order) -> dict:
    return {
        "order_number": order.number, "status": order.status, "subtotal": str(order.subtotal), "discount": str(order.discount),
        "delivery_charge": str(order.delivery_charge), "total": str(order.total), "payment": "Cash on delivery",
        "items": [{"sku": i.sku, "name": i.name, "variant": i.variant_label, "quantity": i.quantity, "unit_price": str(i.unit_price)} for i in order.items.all()],
    }


def place(data: dict, *, dry_run: bool = False) -> tuple[dict, bool]:
    """Returns (summary, created). dry_run prices the order exactly like a real one but saves nothing."""
    channel = str(data.get("channel", "")).lower()
    if channel not in ChatOrder.Channel.values:
        raise ChatOrderError(f"channel must be one of {', '.join(ChatOrder.Channel.values)}")
    request_id = str(data.get("request_id", "")).strip()[:64]
    if not dry_run and not request_id:
        raise ChatOrderError("request_id is required (use the chat message id, so a retry never creates a second order)")
    if not dry_run:
        existing = ChatOrder.objects.select_related("order").filter(request_id=request_id).first()
        if existing is not None:
            return summary(existing.order), False
    try:
        phone = clean_bd_phone(str(data.get("phone", "")))
    except Exception:  # noqa: BLE001  (validator raises a ValidationError with its own wording)
        raise ChatOrderError("That phone number doesn't look right. Ask for a Bangladeshi mobile number like 01712345678") from None
    area = str(data.get("delivery_area", "")).lower()
    if area not in DeliveryArea.values:
        raise ChatOrderError(f"delivery_area must be one of {', '.join(DeliveryArea.values)}")
    name, address, city = (str(data.get(k, "")).strip() for k in ("full_name", "address", "city"))
    if not (name and address and city):
        raise ChatOrderError("full_name, address and city are required")
    lines = resolve_lines(data.get("items"))
    if not dry_run and Order.objects.filter(user__email=f"{phone}@{PLACEHOLDER_DOMAIN}", created_at__gte=timezone.now() - timedelta(days=1)).count() >= MAX_ORDERS_PER_PHONE_PER_DAY:
        raise ChatOrderError("Too many orders from this number today. Please call us.", status=429)
    notes = f"Chat order via {ChatOrder.Channel(channel).label}. {str(data.get('notes', '')).strip()}"[:255]
    order_data = {"full_name": name[:120], "phone": phone, "address": address[:255], "city": city[:80], "delivery_area": area, "notes": notes}
    result: dict = {}
    try:
        with transaction.atomic():
            order = create_order(user=chat_user_for(phone), lines=lines, data=order_data, coupon_code="", auto_confirm=False)
            if not dry_run:
                ChatOrder.objects.create(order=order, channel=channel, channel_user_id=str(data.get("channel_user_id", ""))[:80], request_id=request_id)
            result = summary(order)
            if dry_run:
                raise _Rollback
    except _Rollback:
        pass
    except OrderError as exc:
        raise ChatOrderError(str(exc)) from exc
    return result, not dry_run


def status_for(phone: str, number: str) -> dict | None:
    """An order's status, only when the phone matches the order (so numbers can't be guessed)."""
    try:
        phone = clean_bd_phone(phone)
    except Exception:  # noqa: BLE001
        return None
    order = Order.objects.prefetch_related("items").filter(number__iexact=number.strip(), phone=phone).first()
    if order is None:
        return None
    out = summary(order)
    out.update(status_label=order.get_status_display(), tracking_number=order.tracking_number, placed_at=order.created_at.isoformat())
    return out
