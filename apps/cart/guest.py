"""Abandoned-bag tracking for signed-out visitors. Every function is best-effort: a tracking problem must never break the cart or checkout."""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import GuestCart

logger = logging.getLogger("apps.cart")
SESSION_ID = "guest_cart_id"   # kept in the session itself, so it survives the session key changing at login


def _key(request, create: bool = False) -> str:
    key = request.session.session_key
    if not key and create:
        request.session.create()
        key = request.session.session_key
    return key or ""


def _current(request) -> GuestCart | None:
    pk = request.session.get(SESSION_ID)
    return GuestCart.objects.filter(pk=pk).first() if pk else None


def track(request, data: dict) -> None:
    """Mirror the session bag into GuestCart after every change. An emptied bag keeps its row only when we have a way to contact the visitor."""
    try:
        cart = _current(request)
        if data:
            items = sorted([pid, vid, qty] for (pid, vid), qty in data.items())
            fields = {"session_key": _key(request, create=True), "items": items, "item_count": sum(q for _, _, q in items)}
            if cart is None or cart.order_id:   # a finished order starts a new bag
                cart = GuestCart.objects.create(**fields)
                request.session[SESSION_ID] = cart.pk
            else:
                GuestCart.objects.filter(pk=cart.pk).update(**fields)
            return
        if cart and not cart.has_contact and not cart.order_id:
            cart.delete()
            request.session.pop(SESSION_ID, None)
        elif cart:
            GuestCart.objects.filter(pk=cart.pk).update(items=[], item_count=0)
    except Exception:  # noqa: BLE001
        logger.exception("guest cart tracking failed")


def capture_contact(request, name: str = "", email: str = "", phone: str = "") -> bool:
    """Save what the visitor typed at checkout. Invalid emails are ignored; returns False when there is no bag to attach it to."""
    cart = _current(request)
    if cart is None or not cart.item_count:
        return False
    fields = {}
    if name.strip():
        fields["name"] = name.strip()[:120]
    if email.strip():
        try:
            validate_email(email.strip())
            fields["email"] = email.strip().lower()[:254]
        except ValidationError:
            pass
    digits = "".join(c for c in phone if c.isdigit() or c == "+")
    if len(digits) >= 10:
        fields["phone"] = digits[:20]
    if fields:
        GuestCart.objects.filter(pk=cart.pk).update(**fields)
    return True


def mark_converted(request, order) -> None:
    """The visitor placed an order: remember which one, and keep the contact details we already had."""
    try:
        cart = _current(request)
        if cart:
            GuestCart.objects.filter(pk=cart.pk).update(order=order, name=order.full_name, phone=order.phone, email=order.user.email)
    except Exception:  # noqa: BLE001
        logger.exception("guest cart conversion failed")


def forget(request) -> None:
    """The visitor signed in and their bag moved to their account."""
    pk = request.session.pop(SESSION_ID, None)
    if pk:
        GuestCart.objects.filter(pk=pk, order__isnull=True).delete()
