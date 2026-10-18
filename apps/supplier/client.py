"""Tiny HTTP client for the supplier's REST API (standard library only, keys come from the environment)."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger("apps.supplier")


class SupplierError(Exception):
    """The supplier refused or could not be reached. The message is safe to show to staff."""


def configured() -> bool:
    return bool(settings.SUPPLIER_API_KEY and settings.SUPPLIER_API_SECRET)


def call(method: str, path: str, payload: dict | None = None) -> dict:
    if not configured():
        raise SupplierError("Supplier API keys are not set (SUPPLIER_API_KEY / SUPPLIER_API_SECRET).")
    url = settings.SUPPLIER_API_BASE.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-API-KEY": settings.SUPPLIER_API_KEY, "X-API-SECRET": settings.SUPPLIER_API_SECRET,
        "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NarizMart/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=settings.SUPPLIER_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        logger.warning("supplier %s %s -> HTTP %s", method, path, exc.code)
        raise SupplierError(f"HTTP {exc.code}: {_message(body)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("supplier %s %s unreachable: %s", method, path, exc)
        raise SupplierError("Could not reach the supplier (check internet, IP whitelist and keys).") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise SupplierError("The supplier sent a reply that is not JSON.") from exc


def _message(body: str) -> str:
    try:
        data = json.loads(body)
        return str(data.get("message") or data.get("error") or body)[:250]
    except (ValueError, AttributeError):
        return body[:250]


def balance() -> dict:
    return call("GET", settings.SUPPLIER_PATH_BALANCE).get("data", {})


def products() -> list[dict]:
    return call("GET", settings.SUPPLIER_PATH_PRODUCTS).get("data", [])


def place_order(payload: dict) -> dict:
    return call("POST", settings.SUPPLIER_PATH_ORDERS, payload)


def set_status(supplier_order_id: str, status: str) -> dict:
    return call("POST", settings.SUPPLIER_PATH_STATUS.format(id=supplier_order_id), {"status": status})
