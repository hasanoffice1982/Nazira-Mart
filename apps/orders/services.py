from __future__ import annotations

import logging

from decimal import Decimal

from django.db import connection, transaction
from django.db.models import F

from apps.cart.services import CartService, delivery_charge
from apps.catalog.models import Product, ProductVariant

from .models import Order, OrderItem, OrderStatusHistory
from .notifications import send_new_order_alert, send_status_email

S = Order.Status
TRANSITIONS: dict[str, set[str]] = {
    S.PENDING: {S.CONFIRMED, S.CANCELLED},
    S.CONFIRMED: {S.PROCESSING, S.CANCELLED},
    S.PROCESSING: {S.SHIPPED, S.CANCELLED},
    S.SHIPPED: {S.DELIVERED, S.RETURNED},
    S.DELIVERED: {S.RETURNED},
    S.CANCELLED: set(),
    S.RETURNED: set(),
}
CUSTOMER_CANCELLABLE = {S.PENDING, S.CONFIRMED}


class OrderError(Exception):
    pass


def record_status(order: Order, status: str, *, note: str = "", by=None) -> None:
    OrderStatusHistory.objects.create(order=order, status=status, note=note, changed_by=by)


def lines_from_cart(cart: CartService) -> list[tuple[int, int, int]]:
    """(product_id, variant_id or 0, quantity) triples."""
    return [(l.product.pk, l.variant.pk if l.variant else 0, l.quantity) for l in cart.lines()]


def normalise_lines(lines) -> list[tuple[int, int, int]]:
    """Accept legacy (product_id, qty) pairs from in-flight checkout confirmations."""
    return [(l[0], 0, l[1]) if len(l) == 2 else (l[0], l[1], l[2]) for l in lines]


def _discount_for(user, subtotal: Decimal, coupon_code: str):
    """Re-checked inside the order transaction, with the promotion rows locked so limited offers can't be over-claimed."""
    from .models import Promotion
    from .promotions import evaluate, normalise_code

    ids = list(Promotion.objects.filter(is_active=True).values_list("pk", flat=True))
    if ids:
        list(Promotion.objects.select_for_update().filter(pk__in=ids).values_list("pk", flat=True))  # lock; no-op on SQLite
    result = evaluate(subtotal, user, coupon_code)
    if normalise_code(coupon_code) and result.coupon_error:
        raise OrderError(f"Your coupon couldn't be applied: {result.coupon_error}")
    return result.amount, result.promotion, result.coupon_code


@transaction.atomic
def create_order(*, user, lines, data: dict, billing: dict | None = None, coupon_code: str = "", auto_confirm: bool = True) -> Order:
    """Create an order from (product_id, variant_id, quantity) triples; prices and stock come from the DB."""
    lines = normalise_lines(lines)
    if not lines:
        raise OrderError("Your bag is empty.")

    # Lock rows so concurrent checkouts cannot oversell (no-op on SQLite).
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in={l[0] for l in lines}, is_active=True)}
    # colour/size are optional joins: PostgreSQL only allows FOR UPDATE on them with "OF self"; MariaDB/MySQL do not support "OF" at all
    lock_variants = ProductVariant.objects.select_for_update(of=("self",) if connection.features.has_select_for_update_of else ())
    variants = {v.pk: v for v in lock_variants.filter(pk__in={l[1] for l in lines if l[1]}, is_active=True).select_related("color", "size")}
    # a product with active variants can only be bought through a variant
    has_variants = set(ProductVariant.objects.filter(product_id__in=products, is_active=True).values_list("product_id", flat=True))

    resolved = []
    subtotal = Decimal(0)
    for pid, vid, qty in lines:
        product = products.get(pid)
        if product is None:
            raise OrderError("An item in your bag is no longer available.")
        variant = variants.get(vid) if vid else None
        if vid and (variant is None or variant.product_id != pid):
            raise OrderError(f"“{product.name}” is no longer available in that option.")
        if not vid and pid in has_variants:
            raise OrderError(f"Please choose options for “{product.name}”.")
        stock = variant.stock if variant else product.stock
        title = f"{product.name} ({variant.label})" if variant else product.name
        if stock < qty:
            raise OrderError(f"Sorry, only {stock} of “{title}” left in stock.")
        price = variant.unit_price if variant else product.price
        subtotal += price * qty
        resolved.append((product, variant, price, qty))

    charge = delivery_charge(subtotal, data["delivery_area"])  # free-delivery threshold looks at the products total before any discount
    discount, promotion, applied_code = _discount_for(user, subtotal, coupon_code)
    order = Order.objects.create(
        number=Order.new_number(), user=user, subtotal=subtotal, delivery_charge=charge, total=subtotal - discount + charge,
        discount=discount, promotion=promotion, promotion_label=promotion.name if promotion else "", coupon_code=applied_code,
        full_name=data["full_name"], phone=data["phone"], address=data["address"], city=data["city"],
        delivery_area=data["delivery_area"], notes=data.get("notes", ""),
        # invoice details: the customer's present address when saved, otherwise the delivery contact
        billing_name=(billing or data)["full_name"], billing_phone=(billing or data)["phone"],
        billing_address=(billing or data)["address"], billing_city=(billing or data)["city"],
    )
    for product, variant, price, qty in resolved:
        OrderItem.objects.create(
            order=order, product=product, variant=variant, variant_label=variant.label if variant else "",
            name=product.name, sku=variant.sku if variant else product.sku, art_type=product.art_type,
            unit_price=price, quantity=qty,
        )
        target = variant or product
        target.stock -= qty
        target.save(update_fields=["stock"])
    record_status(order, S.PENDING, note="Order placed — cash on delivery", by=user)
    transaction.on_commit(lambda: send_new_order_alert(order.pk))   # a rolled-back quote never reaches this
    if auto_confirm:   # chat orders are never auto-confirmed: staff phone the customer first
        transaction.on_commit(lambda: _supplier_new_order_hook(order.pk))
    return order


@transaction.atomic
def change_status(order: Order, new_status: str, *, note: str = "", by=None) -> None:
    if new_status == order.status:
        return
    if new_status not in TRANSITIONS[order.status]:
        raise OrderError(f"Cannot move order from {order.get_status_display()} to {Order.Status(new_status).label}.")
    order.status = new_status
    if new_status == S.DELIVERED and order.payment_method == Order.PaymentMethod.COD:
        order.payment_status = Order.PaymentStatus.PAID
    if new_status == S.RETURNED and order.payment_status == Order.PaymentStatus.PAID:
        order.payment_status = Order.PaymentStatus.REFUNDED   # was delivered and paid, then sent back
    order.save(update_fields=["status", "payment_status", "updated_at"])
    if new_status in (S.CANCELLED, S.RETURNED):
        restore_stock(order)
    record_status(order, new_status, note=note, by=by)
    by_customer = bool(by and by.pk == order.user_id and not by.is_staff)
    transaction.on_commit(lambda: send_status_email(order.pk, str(new_status), by_customer=by_customer))
    transaction.on_commit(lambda: _supplier_hook(order.pk, str(new_status)))
    transaction.on_commit(lambda: _dealer_hook(order.pk, str(new_status)))


def _supplier_hook(order_id: int, status: str) -> None:
    """Hand a confirmed order to the supplier integration (does nothing unless it is switched on)."""
    from apps.supplier.services import on_status_change

    try:
        on_status_change(order_id, status)
    except Exception:  # noqa: BLE001  a supplier problem must never break the shop
        logging.getLogger("apps.supplier").exception("supplier hook failed for order id %s", order_id)


def _dealer_hook(order_id: int, status: str) -> None:
    """Hand dealer items of a confirmed order to the dealer's automation (n8n / Make)."""
    from apps.dealers.services import on_status_change

    try:
        on_status_change(order_id, status)
    except Exception:  # noqa: BLE001  a dealer problem must never break the shop
        logging.getLogger("apps.dealers").exception("dealer hook failed for order id %s", order_id)


def _supplier_new_order_hook(order_id: int) -> None:
    from apps.supplier.services import maybe_auto_confirm

    try:
        maybe_auto_confirm(order_id)
    except Exception:  # noqa: BLE001
        logging.getLogger("apps.supplier").exception("auto-confirm failed for order id %s", order_id)


def restore_stock(order: Order) -> None:
    for item in order.items.all():
        if item.variant_id:
            ProductVariant.objects.filter(pk=item.variant_id).update(stock=F("stock") + item.quantity)
        elif item.product_id:
            Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)


def cancel_by_customer(order: Order, user) -> None:
    if order.status not in CUSTOMER_CANCELLABLE:
        raise OrderError("This order can no longer be cancelled.")
    change_status(order, S.CANCELLED, note="Cancelled by customer", by=user)
