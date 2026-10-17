"""Home page "Popular products": admin pins first, then automatic best sellers, newest products fill any gap.

mix    = pinned products, then best sellers (default)
auto   = best sellers only
manual = pinned products only
Until something has sold, the section shows `popular_fallback_count` products (default 4); once there are sales it grows to `popular_count` (default 6).
"""
from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from .models import Product

CACHE_KEY = "popular_ids"   # cleared by Product.save and SiteSettings.save; orders are picked up within the TTL
CACHE_SECONDS = 300


def best_seller_ids(days: int, limit: int) -> list[int]:
    from apps.orders.models import Order, OrderItem

    qs = OrderItem.objects.filter(product__is_active=True).exclude(order__status=Order.Status.CANCELLED)
    if days:
        qs = qs.filter(order__created_at__gte=timezone.now() - timedelta(days=days))
    rows = qs.values("product_id").annotate(units=Sum("quantity")).order_by("-units", "product_id")[:limit]
    return [r["product_id"] for r in rows]


def _compute_ids(site, target_override: int | None = None) -> list[int]:
    active = Product.objects.filter(is_active=True)
    mode = site.popular_mode
    sold = best_seller_ids(site.popular_days, target_override or site.popular_count) if mode != "manual" else []
    target = target_override or (site.popular_count if (sold or mode == "manual") else site.popular_fallback_count)
    ids: list[int] = []
    if mode in ("mix", "manual"):
        ids += list(active.filter(is_popular_pick=True).order_by("popular_order", "id").values_list("id", flat=True))
    if mode in ("mix", "auto"):
        ids += [i for i in sold if i not in ids]
    ids = ids[:target]
    explicit_manual = mode == "manual" and ids
    if len(ids) < target and not explicit_manual:
        ids += list(active.exclude(id__in=ids).order_by("-created_at", "-id").values_list("id", flat=True)[: target - len(ids)])
    return ids


def popular_products(site) -> list[Product]:
    if not site.show_popular:
        return []
    ids = cache.get(CACHE_KEY)
    if ids is None:
        ids = _compute_ids(site)
        cache.set(CACHE_KEY, ids, CACHE_SECONDS)
    products = {
        p.id: p
        for p in Product.objects.filter(id__in=ids, is_active=True).select_related("category").prefetch_related("variants__color", "variants__size", "images")
    }
    return [products[i] for i in ids if i in products]

