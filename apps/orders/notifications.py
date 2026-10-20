"""Customer emails for every order status change (the 'placed' email is the order confirmation)."""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from apps.core.models import SiteSettings

from .models import Order

logger = logging.getLogger("apps.orders")

SUBJECTS = {
    Order.Status.CONFIRMED: "Your order {number} is confirmed",
    Order.Status.PROCESSING: "We're packing your order {number}",
    Order.Status.SHIPPED: "Your order {number} is on its way",
    Order.Status.DELIVERED: "Your order {number} was delivered",
    Order.Status.CANCELLED: "Your order {number} was cancelled",
}


def send_status_email(order_id: int, status: str, *, by_customer: bool = False) -> bool:
    """Send the email for `status`. Loads the order fresh so fields saved in the same request (e.g. a tracking number) are included."""
    subject = SUBJECTS.get(status)
    order = Order.objects.select_related("user", "chat_order").prefetch_related("items").filter(pk=order_id).first()
    if subject is None or order is None:
        return False
    chat = getattr(order, "chat_order", None)
    recipient = chat.contact_email if chat else order.user.email
    if not recipient or recipient.endswith("@chat.narizmart.invalid"):
        return False   # chat customers only get emails when they gave one in the chat
    ctx = {
        "order": order, "status": str(status), "by_customer": by_customer, "brand": settings.SITE_NAME,
        "currency": SiteSettings.load_fresh().currency_symbol, "order_url": f"{settings.SITE_URL.rstrip('/')}{order.get_absolute_url()}",
        "contact_url": f"{settings.SITE_URL.rstrip('/')}/contact/?order={order.number}",
    }
    try:
        send_mail(
            subject=f"{subject.format(number=order.number)} — {settings.SITE_NAME}",
            message=render_to_string("orders/email/status.txt", ctx),
            html_message=render_to_string("orders/email/status.html", ctx),
            from_email=None,
            recipient_list=[recipient],
        )
    except Exception:  # noqa: BLE001  (a mail outage must never block order processing)
        logger.exception("status email failed order=%s status=%s", order.number, status)
        return False
    return True
