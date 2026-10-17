from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger("apps.accounts")
User = get_user_model()
VERIFY_SALT = "accounts.verify-email"


def _send(user, subject: str, template: str, context: dict) -> None:
    ctx = {"user": user, "brand": settings.SITE_NAME, **context}
    send_mail(
        subject=subject,
        message=render_to_string(f"accounts/email/{template}.txt", ctx),
        html_message=render_to_string(f"accounts/email/{template}.html", ctx),
        from_email=None,
        recipient_list=[user.email],
    )


def make_verify_token(user) -> str:
    return signing.dumps({"u": user.pk, "e": user.email}, salt=VERIFY_SALT)


def user_from_verify_token(token: str):
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFY_MAX_AGE)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data["u"], email=data["e"], is_active=True).first()


def send_verification_email(request, user) -> bool:
    """Returns False (and logs) instead of raising so a mail outage never breaks signup."""
    url = request.build_absolute_uri(reverse("accounts:verify", args=[make_verify_token(user)]))
    try:
        _send(user, f"Verify your {settings.SITE_NAME} email", "verify_email", {"verify_url": url})
        return True
    except Exception:  # noqa: BLE001
        logger.exception("verification email failed for user=%s", user.pk)
        return False


def send_password_changed_email(request, user) -> None:
    try:
        _send(user, "Your password was changed", "password_changed", {"reset_url": request.build_absolute_uri(reverse("accounts:password_reset"))})
    except Exception:  # noqa: BLE001
        logger.exception("password-changed email failed for user=%s", user.pk)


def mark_email_verified(user) -> None:
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])
