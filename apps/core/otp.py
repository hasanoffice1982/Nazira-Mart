"""Shared primitives for emailed one-time codes (checkout confirmation, sign-in 2FA)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings

TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
MAX_RESENDS = 3
RESEND_COOLDOWN = timedelta(seconds=60)


class OTPError(Exception):
    """User-presentable failure."""


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_code(obj, code: str) -> str:
    """HMAC bound to the record id, so a code only ever validates against its own record."""
    return hmac.new(settings.SECRET_KEY.encode(), f"{type(obj).__name__}:{obj.pk}:{code}".encode(), hashlib.sha256).hexdigest()


def codes_match(obj, code: str) -> bool:
    return hmac.compare_digest(obj.code_hash, hash_code(obj, (code or "").strip()))


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'•' * max(len(name) - 2, 1)}@{domain}"


def wrong_code_message(attempts_used: int) -> str:
    left = MAX_ATTEMPTS - attempts_used
    if left <= 0:
        return "Too many wrong attempts. Request a new code."
    return f"That code isn't right. {left} attempt{'s' if left != 1 else ''} left."
