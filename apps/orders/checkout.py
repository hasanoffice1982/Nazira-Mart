"""Checkout orchestration: direct for verified customers, email-code confirmation for everyone else."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts import addresses as address_book
from apps.accounts import twofactor
from apps.accounts.models import PresentAddress, ShippingAddress
from apps.cart.services import get_cart
from apps.core.models import SiteSettings

from . import otp as otp_service
from .promotions import COUPON_SESSION_KEY
from .models import CheckoutOTP, Order
from .services import OrderError, create_order, lines_from_cart, normalise_lines

logger = logging.getLogger("apps.orders")
User = get_user_model()
SESSION_KEY = "checkout_otp"
ADDRESS_FIELDS = ("full_name", "phone", "address", "city", "delivery_area", "notes")


def needs_email_confirmation(user) -> bool:
    """Signed-in customers skip the emailed code when they verified their email, or when the account came from a guest checkout
    (no password yet: its owner proves the inbox later through the set-password link in the order email)."""
    return not (user.is_authenticated and (user.email_verified or not user.has_usable_password()))


def email_has_account(email: str) -> bool:
    return User.objects.filter(email__iexact=email.strip()).exists()


def address_data(cleaned: dict) -> dict:
    return {k: cleaned.get(k, "") for k in ADDRESS_FIELDS}


def billing_for(user, data: dict) -> dict:
    present = PresentAddress.objects.filter(user=user).first()
    return present.as_data() if present else data


def save_new_shipping(user, data: dict, *, make_default: bool = False) -> bool:
    """Add a checkout address to the address book; silently skip when the 10-address cap is reached."""
    fields = {k: data[k] for k in ("full_name", "phone", "address", "city", "delivery_area")} | {"notes": data.get("notes", "")}
    try:
        address_book.save_shipping(user, ShippingAddress(label=ShippingAddress.Label.HOME, **fields), make_default=make_default)
    except address_book.AddressLimitError:
        return False
    return True


def begin_confirmation(request, email: str, cleaned: dict) -> CheckoutOTP:
    """Validate the target email, park the checkout, and email the code. Nothing is created yet."""
    existing = User.objects.filter(email__iexact=email).first()
    if existing and (existing.is_staff or existing.is_superuser or not existing.is_active):
        raise OrderError("Please use a different email address for your order.")
    lines = lines_from_cart(get_cart(request))
    if not lines:
        raise OrderError("Your bag is empty.")
    try:
        otp = otp_service.start(email, {"address": address_data(cleaned), "lines": lines, "save_address": bool(cleaned.get("save_address")), "coupon": request.session.get(COUPON_SESSION_KEY, "")})
    except otp_service.OTPError as exc:
        raise OrderError(str(exc))
    request.session[SESSION_KEY] = str(otp.pk)
    return otp


def pending_otp(request) -> CheckoutOTP | None:
    token = request.session.get(SESSION_KEY)
    if not token:
        return None
    try:
        otp = CheckoutOTP.objects.filter(pk=token, consumed_at__isnull=True).first()
    except (ValueError, TypeError):  # malformed uuid in session
        return None
    return otp


@transaction.atomic
def complete_confirmation(request, otp: CheckoutOTP) -> Order:
    """Called after the code is verified: create/attach the account, place the order, log the customer in."""
    otp = CheckoutOTP.objects.select_for_update().get(pk=otp.pk)
    if otp.consumed_at:
        raise OrderError("This checkout was already completed.")
    data, lines = otp.payload["address"], normalise_lines(otp.payload["lines"])

    user = User.objects.filter(email__iexact=otp.email).first()
    new_account = user is None
    if user and (user.is_staff or user.is_superuser or not user.is_active):
        raise OrderError("Please use a different email address for your order.")
    if new_account:
        user = User(email=otp.email, first_name=data["full_name"].split()[0][:150], phone=data["phone"])
        user.set_unusable_password()  # the customer chooses one via the emailed link
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save()
    elif not user.email_verified:  # entering the emailed code proves they own the inbox
        user.email_verified, user.email_verified_at = True, timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])

    order = create_order(user=user, lines=lines, data=data, billing=billing_for(user, data), coupon_code=otp.payload.get("coupon", ""))
    if new_account or otp.payload.get("save_address"):
        save_new_shipping(user, data)  # first address becomes the default automatically
    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at"])

    get_cart(request).clear()  # clear the guest cart BEFORE login so it isn't merged back in
    request.session.pop(SESSION_KEY, None)
    request.session.pop(COUPON_SESSION_KEY, None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    twofactor.mark_verified(request)  # they just entered an emailed code; don't ask for a second one
    transaction.on_commit(lambda: send_order_confirmation(request, order, new_account))
    return order


def place_guest_order(request, email: str, cleaned: dict) -> Order:
    """First-time guest (an email we have never seen): no emailed code, so checkout is one smooth step.
    Creates the account without a password, places the order and signs the guest in; the order email carries a set-password link.
    Existing emails still confirm by code (ConfirmCheckoutView), otherwise anyone could sign in to someone else's account."""
    email, data = email.strip().lower(), address_data(cleaned)
    with transaction.atomic():
        user = User(email=email, first_name=data["full_name"].split()[0][:150], phone=data["phone"])
        user.set_unusable_password()
        user.save()
        order = create_order(user=user, lines=lines_from_cart(get_cart(request)), data=data, billing=data, coupon_code=request.session.get(COUPON_SESSION_KEY, ""))
        save_new_shipping(user, data)   # the first address becomes the default
        transaction.on_commit(lambda: send_order_confirmation(request, order, True))
    get_cart(request).clear()   # clear the guest cart BEFORE login so it isn't merged back in
    request.session.pop(COUPON_SESSION_KEY, None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    twofactor.mark_verified(request)
    return order


def send_order_confirmation(request, order: Order, new_account: bool = False) -> None:
    ctx = {
        "order": order, "brand": settings.SITE_NAME, "currency": SiteSettings.load_fresh().currency_symbol,
        "order_url": request.build_absolute_uri(order.get_absolute_url()), "set_password_url": "",
    }
    if new_account:
        user = order.user
        ctx["set_password_url"] = request.build_absolute_uri(
            reverse("accounts:password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)])
        )
    try:
        send_mail(
            subject=f"Order {order.number} confirmed — {settings.SITE_NAME}",
            message=render_to_string("orders/email/confirmation.txt", ctx),
            html_message=render_to_string("orders/email/confirmation.html", ctx),
            from_email=None,
            recipient_list=[order.user.email],
        )
    except Exception:  # noqa: BLE001
        logger.exception("order confirmation email failed order=%s", order.number)
