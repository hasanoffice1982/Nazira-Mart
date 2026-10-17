"""Single source of truth for the client IP and lockout username (used by axes + ratelimit).

Always returns a *valid* IP address string: axes stores it in a database inet column on PostgreSQL, which rejects
anything else ("unknown", an empty string, a spoofed header value, or a unix-socket path).
"""
import ipaddress

from django.conf import settings

FALLBACK = "0.0.0.0"  # noqa: S104 - a placeholder meaning "unknown", never bound to


def _valid(value: str) -> str | None:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request) -> str:
    if getattr(settings, "USE_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            ip = _valid(forwarded.split(",")[-1])  # rightmost = set by our own Nginx, so it can't be spoofed
            if ip:
                return ip
    return _valid(request.META.get("REMOTE_ADDR", "")) or FALLBACK


def axes_username(request, credentials) -> str:
    credentials = credentials or {}
    # axes passes {"username": ...} from forms but {<USERNAME_FIELD>: ...} from the auth backend
    return (credentials.get("username") or credentials.get("email") or "").strip().lower()
