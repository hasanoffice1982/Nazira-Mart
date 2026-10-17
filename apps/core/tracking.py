"""Advertising / analytics tags configured in Admin -> Site settings: CSP allowances and the event payloads sent to them."""
from __future__ import annotations

from decimal import Decimal


def csp_update(site) -> dict[str, list[str]]:
    """Extra Content-Security-Policy sources, added only for the tags that are switched on."""
    update: dict[str, list[str]] = {}

    def add(directive: str, *sources: str) -> None:
        update.setdefault(directive, [])
        update[directive] += [s for s in sources if s not in update[directive]]

    if site.meta_pixel_id:
        add("script-src", "https://connect.facebook.net")
        add("connect-src", "https://www.facebook.com", "https://connect.facebook.net")
        add("img-src", "https://www.facebook.com")
    if site.google_tag_id:
        add("script-src", "https://www.googletagmanager.com", "https://www.google-analytics.com")
        add("connect-src", "https://www.google-analytics.com", "https://*.google-analytics.com", "https://*.analytics.google.com",
            "https://*.googletagmanager.com", "https://*.g.doubleclick.net", "https://www.google.com")
        add("img-src", "https://www.google-analytics.com", "https://*.google-analytics.com", "https://www.googletagmanager.com",
            "https://*.g.doubleclick.net", "https://www.google.com")
        add("frame-src", "https://www.googletagmanager.com")
    return update


def _num(value) -> float:
    return float(Decimal(value))


def product_event(name: str, product, currency_code: str, *, quantity: int = 1, price=None) -> dict:
    price = product.from_price if price is None else price
    return {
        "event": name, "currency": currency_code, "value": _num(price) * quantity,
        "items": [{"id": str(product.pk), "name": product.name, "category": product.category.name, "price": _num(price), "quantity": quantity}],
    }


def lines_event(name: str, lines, currency_code: str, **extra) -> dict:
    items = [{"id": str(l.product.pk), "name": l.product.name, "category": l.product.category.name, "price": _num(l.unit_price), "quantity": l.quantity} for l in lines]
    return {"event": name, "currency": currency_code, "value": sum(i["price"] * i["quantity"] for i in items), "items": items, **extra}


def order_event(order, currency_code: str) -> dict:
    items = [{"id": str(i.product_id or i.sku), "name": i.name, "price": _num(i.unit_price), "quantity": i.quantity} for i in order.items.all()]
    return {"event": "purchase", "currency": currency_code, "value": _num(order.total), "transaction_id": order.number, "items": items}
