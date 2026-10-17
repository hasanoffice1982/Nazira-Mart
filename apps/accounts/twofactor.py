"""Email-code two-factor sign-in.

Flow: password login (axes-protected) -> `user_logged_in` marks the session *unverified* when the user needs
2FA -> TwoFactorMiddleware blocks every page except the challenge -> correct emailed code marks it verified.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.otp import (
    MAX_ATTEMPTS, MAX_RESENDS, RESEND_COOLDOWN, TTL, OTPError, codes_match, hash_code, new_code, wrong_code_message,
)

from .models import LoginCode

logger = logging.getLogger("apps.accounts")

VERIFIED_KEY = "2fa_verified"
NEXT_KEY = "2fa_next"
TRUST_COOKIE = "bm_trusted"
TRUST_SALT = "accounts.trusted-device"
TRUST_DAYS = 30


# ---- session state ------------------------------------------------------------
def is_verified(request) -> bool:
    return bool(request.session.get(VERIFIED_KEY))


def mark_verified(request) -> None:
    request.session[VERIFIED_KEY] = True
    request.session.pop(NEXT_KEY, None)


def needs_challenge(request) -> bool:
    user = request.user
    return user.is_authenticated and user.requires_two_factor and not is_verified(request)


def remember_next(request, path: str) -> None:
    if url_has_allowed_host_and_scheme(path, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        request.session[NEXT_KEY] = path


def pop_next(request, default: str) -> str:
    return request.session.pop(NEXT_KEY, None) or default


# ---- trusted devices (customers only; staff are always challenged) ----------------
def _trust_value(user) -> str:
    # bound to the password hash: changing the password revokes every trusted device
    return signing.get_cookie_signer(salt=TRUST_SALT).sign(f"{user.pk}:{user.get_session_auth_hash()}")


def set_trusted_cookie(response, user) -> None:
    if user.is_staff or user.is_superuser:
        return
    response.set_cookie(
        TRUST_COOKIE, _trust_value(user), max_age=TRUST_DAYS * 86400, httponly=True,
        secure=not settings.DEBUG, samesite="Lax",
    )


def is_trusted_device(request, user) -> bool:
    if user.is_staff or user.is_superuser:
        return False
    raw = request.COOKIES.get(TRUST_COOKIE)
    if not raw:
        return False
    try:
        value = signing.get_cookie_signer(salt=TRUST_SALT).unsign(raw, max_age=TRUST_DAYS * 86400)
    except signing.BadSignature:
        return False
    return value == f"{user.pk}:{user.get_session_auth_hash()}"


# ---- emailed codes ---------------------------------------------------------------
def current_code(user) -> LoginCode | None:
    return user.login_codes.filter(consumed_at__isnull=True, expires_at__gt=timezone.now()).order_by("-created_at").first()


def _issue_and_send(code_obj: LoginCode) -> None:
    code = new_code()
    now = timezone.now()
    code_obj.code_hash = hash_code(code_obj, code)
    code_obj.attempts = 0
    code_obj.last_sent_at = now
    code_obj.expires_at = now + TTL
    code_obj.save(update_fields=["code_hash", "attempts", "last_sent_at", "expires_at"])
    ctx = {"code": code, "minutes": int(TTL.total_seconds() // 60), "brand": settings.SITE_NAME}
    send_mail(
        subject=f"{code} is your {settings.SITE_NAME} sign-in code",
        message=render_to_string("accounts/email/login_code.txt", ctx),
        html_message=render_to_string("accounts/email/login_code.html", ctx),
        from_email=None,
        recipient_list=[code_obj.user.email],
    )


def start(user) -> LoginCode:
    """Create and email a fresh code (older unused codes are invalidated)."""
    user.login_codes.filter(consumed_at__isnull=True).update(consumed_at=timezone.now())
    code_obj = LoginCode.objects.create(user=user, expires_at=timezone.now() + TTL)
    try:
        _issue_and_send(code_obj)
    except Exception:  # noqa: BLE001
        logger.exception("login code email failed user=%s", user.pk)
        code_obj.delete()
        raise OTPError("We couldn't send your sign-in code. Please try again shortly.")
    return code_obj


def resend(code_obj: LoginCode) -> None:
    if code_obj.resends >= MAX_RESENDS:
        raise OTPError("Too many codes requested. Please sign in again.")
    if code_obj.last_sent_at:
        wait = (code_obj.last_sent_at + RESEND_COOLDOWN - timezone.now()).total_seconds()
        if wait > 0:
            raise OTPError(f"Please wait {int(wait) + 1}s before requesting another code.")
    code_obj.resends += 1
    code_obj.save(update_fields=["resends"])
    try:
        _issue_and_send(code_obj)
    except Exception:  # noqa: BLE001
        logger.exception("login code resend failed user=%s", code_obj.user_id)
        raise OTPError("We couldn't send the email right now. Please try again shortly.")


def verify(code_obj: LoginCode, code: str) -> None:
    if code_obj.consumed_at:
        raise OTPError("This code has already been used.")
    if timezone.now() >= code_obj.expires_at:
        raise OTPError("This code has expired. Request a new one.")
    if code_obj.attempts >= MAX_ATTEMPTS:
        raise OTPError("Too many wrong attempts. Request a new code.")
    code_obj.attempts += 1
    code_obj.save(update_fields=["attempts"])
    if not codes_match(code_obj, code):
        raise OTPError(wrong_code_message(code_obj.attempts))
    code_obj.consumed_at = timezone.now()
    code_obj.save(update_fields=["consumed_at"])


def purge_expired() -> int:
    deleted, _ = LoginCode.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
    return deleted
