"""Email one-time codes that confirm a guest checkout before any order, stock change or account is created."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.otp import (  # noqa: F401  (re-exported for callers/tests)
    MAX_ATTEMPTS, MAX_RESENDS, RESEND_COOLDOWN, TTL, OTPError, codes_match, hash_code, mask_email, new_code, wrong_code_message,
)

from .models import CheckoutOTP

logger = logging.getLogger("apps.orders")



def _issue_and_send(otp: CheckoutOTP) -> None:
    code = new_code()
    now = timezone.now()
    otp.code_hash = hash_code(otp, code)
    otp.attempts = 0
    otp.last_sent_at = now
    otp.expires_at = now + TTL
    otp.save(update_fields=["code_hash", "attempts", "last_sent_at", "expires_at"])
    ctx = {"code": code, "minutes": int(TTL.total_seconds() // 60), "brand": settings.SITE_NAME}
    send_mail(
        subject=f"{code} is your {settings.SITE_NAME} confirmation code",
        message=render_to_string("orders/email/otp.txt", ctx),
        html_message=render_to_string("orders/email/otp.html", ctx),
        from_email=None,
        recipient_list=[otp.email],
    )


def start(email: str, payload: dict) -> CheckoutOTP:
    otp = CheckoutOTP.objects.create(email=email.lower(), payload=payload, code_hash="", expires_at=timezone.now() + TTL)
    try:
        _issue_and_send(otp)
    except Exception:  # noqa: BLE001
        logger.exception("checkout OTP email failed")
        otp.delete()
        raise OTPError("We couldn't send the confirmation email. Please check the address and try again.")
    return otp


def resend(otp: CheckoutOTP) -> None:
    if otp.consumed_at or otp.resends >= MAX_RESENDS:
        raise OTPError("Too many codes requested. Please start checkout again.")
    wait = (otp.last_sent_at + RESEND_COOLDOWN - timezone.now()).total_seconds() if otp.last_sent_at else 0
    if wait > 0:
        raise OTPError(f"Please wait {int(wait) + 1}s before requesting another code.")
    otp.resends += 1
    otp.save(update_fields=["resends"])
    try:
        _issue_and_send(otp)
    except Exception:  # noqa: BLE001
        logger.exception("checkout OTP resend failed")
        raise OTPError("We couldn't send the email right now. Please try again shortly.")


def verify(otp: CheckoutOTP, code: str) -> None:
    """Raises OTPError unless `code` is right, unexpired, unused and within the attempt budget."""
    if otp.consumed_at:
        raise OTPError("This code has already been used.")
    if timezone.now() >= otp.expires_at:
        raise OTPError("This code has expired. Request a new one.")
    if otp.attempts >= MAX_ATTEMPTS:
        raise OTPError("Too many wrong attempts. Request a new code.")
    otp.attempts += 1
    otp.save(update_fields=["attempts"])
    if not codes_match(otp, code):
        raise OTPError(wrong_code_message(otp.attempts))


def purge_expired() -> int:
    cutoff = timezone.now() - timedelta(days=1)
    deleted, _ = CheckoutOTP.objects.filter(expires_at__lt=cutoff).delete()
    return deleted
