"""Import and refresh products from the supplier catalogue.

New products are created HIDDEN (is_active=False) so nothing goes live without your review, unless `activate=True`.
Prices follow the supplier's `final_selling_price`, because the courier collects exactly that amount on delivery.
Existing products only get their price and stock refreshed (your edits to name, text and photos are kept).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from apps.catalog.models import Category, Product, ProductImage

from . import client
from .models import SupplierProduct
from .variants import create_variants, refresh_stock

logger = logging.getLogger("apps.supplier")

# (category slug, keywords in the title). First match wins, so keep the specific ones first.
CATEGORY_KEYWORDS = [
    ("jewellery", ("earring", "necklace", "pendant", "bangle", "churi", "bracelet", "jewel", "ring set", "tikli", "pearl")),
    ("toys", ("toy", "game", "puzzle", "blocks", "doll ", "bubble", "flash card", "drawing", "learning")),
    ("kids", ("baby", "girls", "boys", "kids", "children", "toddler", "newborn", "infant", "school")),
    ("bags", ("handbag", "crossbody", "backpack", "purse", "ladies bag", "women's bag", "clutch")),
    ("shoes", ("heel", "sandal", "shoe", "slipper", "sneaker", "boot")),
    ("beauty", ("lipstick", "skin", "cream", "serum", "makeup", "perfume", "pedicure", "hair", "beauty")),
    ("women", ("ladies", "women", "womens", "kurti", "saree", "dress", "hijab", "abaya", "burka", "scarf")),
]
MAX_IMAGES = 5
MAX_IMAGE_BYTES = 4_500_000


@dataclass
class Report:
    seen: int = 0
    created: int = 0
    updated: int = 0
    linked: int = 0
    skipped: int = 0
    images: int = 0
    notes: list[str] = field(default_factory=list)


def guess_category(title: str) -> str | None:
    t = f" {title.lower()} "
    for slug, words in CATEGORY_KEYWORDS:
        if any(w in t for w in words):
            return slug
    return None


def _dec(value) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def image_urls(item: dict) -> list[str]:
    urls: list[str] = []
    thumb = (item.get("thumbnail") or "").strip()
    if thumb:
        urls.append(thumb)
    raw = item.get("images")
    try:
        names = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        names = []
    base = thumb.rsplit("/", 1)[0] + "/" if "/" in thumb else ""
    for name in names:
        if isinstance(name, str) and name.strip():
            url = name if name.startswith("http") else urljoin(base, name)
            if url not in urls:
                urls.append(url)
    return urls[:MAX_IMAGES]


def download(url: str) -> bytes | None:
    if not url.startswith("https://"):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NarizMart/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except Exception:  # noqa: BLE001
        logger.warning("could not download %s", url)
        return None
    return data if 0 < len(data) <= MAX_IMAGE_BYTES else None


def _add_photos(product: Product, item: dict, report: Report) -> None:
    for pos, url in enumerate(image_urls(item)):
        data = download(url)
        if not data:
            continue
        name = f"{product.slug[:60]}-{pos + 1}.jpg"
        try:
            gallery = ProductImage(product=product, position=pos, alt_text=product.name[:160])
            gallery.image.save(name, ContentFile(data), save=False)
            gallery.full_clean()
            gallery.save()
        except Exception:  # noqa: BLE001  one bad picture must not stop the import
            logger.warning("skipped a bad picture for %s", product.slug)
            continue
        report.images += 1
        if pos == 0 and not product.image:
            product.image.save(name, ContentFile(data), save=True)


def sync(*, items: list[dict] | None = None, include: str = "", exclude: str = "", limit: int = 0, activate: bool = False,
         photos: bool = True, only_known_category: bool = True, min_stock: int = 1, dry_run: bool = False, create_new: bool = True) -> Report:
    report = Report()
    catalog = items if items is not None else client.products()
    inc = re.compile(include, re.I) if include else None
    exc = re.compile(exclude, re.I) if exclude else None
    existing = {sp.supplier_product_id: sp for sp in SupplierProduct.objects.select_related("product")}
    categories = {c.slug: c for c in Category.objects.all()}
    by_sku = {p.sku: p for p in Product.objects.filter(supplier_link__isnull=True).exclude(sku="")}   # products uploaded by CSV, SKU = supplier product id
    for item in catalog:
        sid = str(item.get("product_id") or "").strip()
        title = str(item.get("title") or "").strip()
        report.seen += 1
        price = _dec(item.get("final_selling_price"))
        if not sid or not title or price is None:
            report.skipped += 1
            continue
        stock = int(item.get("stock") or 0)
        link = existing.get(sid)
        if link is None and sid in by_sku:   # same identifier as a CSV-uploaded product: connect them instead of creating a duplicate
            if not dry_run:
                link = SupplierProduct.objects.create(
                    product=by_sku[sid], supplier_product_id=sid, base_price=_dec(item.get("base_price")),
                    commission=_dec(item.get("partner_commission")), final_price=price, supplier_stock=max(stock, 0),
                )
                existing[sid] = link
            report.linked += 1
            if dry_run:
                report.notes.append(f"{sid} would be linked to existing product {by_sku[sid].name[:50]}")
                continue
        if link:   # refresh price and stock only
            if dry_run:
                report.updated += 1
                continue
            product = link.product
            changed = []
            if product.price != price:
                product.price = price
                changed.append("price")
            if product.stock != stock:
                product.stock = stock
                changed.append("stock")
            if not item.get("is_active", 1) and product.is_active:
                product.is_active = False
                changed.append("is_active")
            if changed:
                product.save(update_fields=changed)
            if "stock" in changed:
                refresh_stock(product, stock)
            link.base_price, link.commission, link.final_price, link.supplier_stock = _dec(item.get("base_price")), _dec(item.get("partner_commission")), price, max(stock, 0)
            link.save()
            report.updated += 1
            continue
        if not create_new:   # refresh-only run: never add products by itself
            report.skipped += 1
            continue
        if (inc and not inc.search(title)) or (exc and exc.search(title)) or stock < min_stock or not item.get("is_active", 1):
            report.skipped += 1
            continue
        slug_cat = guess_category(title)
        if slug_cat is None and only_known_category:
            report.skipped += 1
            continue
        if limit and report.created >= limit:
            report.skipped += 1
            continue
        if dry_run:
            report.created += 1
            report.notes.append(f"{sid} [{slug_cat or 'other'}] {title[:60]} ৳{price}")
            continue
        category = categories.get(slug_cat or "other")
        if category is None:
            category = Category.objects.create(slug=slug_cat or "other", name=(slug_cat or "other").title(), title=(slug_cat or "other").title(), description="")
            categories[category.slug] = category
        with transaction.atomic():
            slug = f"{slugify(title)[:200]}-{sid.lower()}"
            product = Product.objects.create(
                category=category, name=title[:255], slug=slug[:255], sku=sid[:40], description=str(item.get("description") or "")[:4000],
                art_type="handbag", price=price, stock=max(stock, 0), is_active=activate,
            )
            SupplierProduct.objects.create(
                product=product, supplier_product_id=sid, base_price=_dec(item.get("base_price")), commission=_dec(item.get("partner_commission")),
                final_price=price, supplier_stock=max(stock, 0),
            )
            create_variants(product, product.description, stock)
        if photos:
            _add_photos(product, item, report)
        report.created += 1
    return report
