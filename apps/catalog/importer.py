"""Bulk product + variant import from CSV (one row per variant).

Everything is validated first; `apply` only runs on a fully valid file and is a single transaction,
so a bad row can never leave the catalog half-imported.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.text import slugify

from . import image_import
from .models import Category, Color, PendingPhoto, Product, ProductVariant, Size

MAX_BYTES = 1_000_000
MAX_ROWS = 2000
COLUMNS = [
    "name", "slug", "sku", "category", "description", "price", "old_price", "badge", "stock", "is_active",
    "color", "color_hex", "size", "variant_sku", "variant_price", "variant_stock", "images", "color_images",
]
REQUIRED = {"name", "category", "price"}
TRUE, FALSE = {"1", "true", "yes", "y", "active"}, {"0", "false", "no", "n", "inactive"}
SLUG_MAX = 255   # Product.slug / Category.slug length
NAME_MAX = 255   # Product.name length
CATEGORY_NAME_MAX = 80
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEMO_ROWS = [
    # product with colour + size variants (per-variant stock, one variant with its own price)
    ["Classic Cotton T-Shirt", "classic-cotton-tshirt", "NM-1001", "Fashion", "Soft everyday tee.", "850", "1050", "NEW DROP", "", "yes", "Black", "#1c3129", "M", "", "", "25"],
    ["Classic Cotton T-Shirt", "classic-cotton-tshirt", "", "", "", "", "", "", "", "", "Black", "#1c3129", "L", "", "", "18"],
    ["Classic Cotton T-Shirt", "classic-cotton-tshirt", "", "", "", "", "", "", "", "", "White", "#f2f2f2", "M", "", "", "30"],
    ["Classic Cotton T-Shirt", "classic-cotton-tshirt", "", "", "", "", "", "", "", "", "White", "#f2f2f2", "XL", "TSHIRT-WHT-XL", "950", "12"],
    # product with sizes only
    ["Everyday Running Shoes", "everyday-running-shoes", "NM-1002", "Fashion", "Light and breathable.", "3200", "3900", "HOT PICK", "", "yes", "", "", "41", "", "", "10"],
    ["Everyday Running Shoes", "everyday-running-shoes", "", "", "", "", "", "", "", "", "", "", "42", "", "", "14"],
    ["Everyday Running Shoes", "everyday-running-shoes", "", "", "", "", "", "", "", "", "", "", "43", "", "", "8"],
    # product with colours only
    ["Ceramic Coffee Mug", "ceramic-coffee-mug", "NM-1003", "Home", "350 ml glazed mug.", "450", "", "", "", "yes", "Teal", "#3b9189", "", "", "", "40"],
    ["Ceramic Coffee Mug", "ceramic-coffee-mug", "", "", "", "", "", "", "", "", "Sand", "#d8c9a6", "", "", "", "35"],
    # simple product without variants (uses the stock column)
    ["Bamboo Desk Organizer", "bamboo-desk-organizer", "NM-1004", "Home", "Keeps your desk tidy.", "1290", "1590", "", "60", "yes", "", "", "", "", "", "", "", ""],
]


@dataclass
class VariantSpec:
    color: str = ""
    color_hex: str = ""
    size: str = ""
    sku: str = ""
    price: Decimal | None = None
    stock: int = 0


@dataclass
class ProductSpec:
    key: str
    line: int
    name: str = ""
    slug: str = ""
    sku: str = ""
    category: str = ""
    description: str = ""
    price: Decimal = Decimal(0)
    old_price: Decimal | None = None
    badge: str = ""
    stock: int = 0
    is_active: bool = True
    variants: list[VariantSpec] = field(default_factory=list)
    exists: bool = False
    images: list[str] = field(default_factory=list)                    # shown for every color
    color_images: dict[str, list[str]] = field(default_factory=dict)   # color name -> photos for that color
    pending_images: list[str] = field(default_factory=list)            # file names not uploaded yet: saved, attached automatically later
    pending_color_images: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Plan:
    products: list[ProductSpec] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    new_categories: set[str] = field(default_factory=set)
    new_colors: set[str] = field(default_factory=set)
    new_sizes: set[str] = field(default_factory=set)
    zip_path: str = ""

    @property
    def image_entries(self) -> list[dict]:
        return [
            {"slug": p.slug, "shared": p.images, "colors": p.color_images}
            for p in self.products if p.images or p.color_images
        ]

    @property
    def pending_count(self) -> int:
        return sum(len(p.pending_images) + sum(len(v) for v in p.pending_color_images.values()) for p in self.products)

    @property
    def image_count(self) -> int:
        return sum(len(e["shared"]) + sum(len(v) for v in e["colors"].values()) for e in self.image_entries)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.products)

    @property
    def stats(self) -> dict:
        creates = [p for p in self.products if not p.exists]
        return {
            "products": len(self.products), "new": len(creates), "updated": len(self.products) - len(creates),
            "variants": sum(len(p.variants) for p in self.products), "skipped": len(self.skipped),
            "images": self.image_count, "pending": self.pending_count,
        }


def demo_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    writer.writerows(row + [""] * (len(COLUMNS) - len(row)) for row in DEMO_ROWS)
    return out.getvalue()


def _fit_slug(key: str) -> str:
    """A slug derived from a long name is shortened to fit, with a stable hash so two long names never collide."""
    if len(key) <= SLUG_MAX:
        return key
    return f"{key[:SLUG_MAX - 7].rstrip('-')}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def _decimal(raw: str, label: str, errors: list, line: int, *, required=False) -> Decimal | None:
    raw = (raw or "").strip().replace(",", "")
    if not raw:
        if required:
            errors.append((line, f"{label} is required"))
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append((line, f"{label} “{raw}” is not a number"))
        return None
    if value < 0 or value != value.quantize(Decimal("0.01")) or value >= Decimal("100000000"):
        errors.append((line, f"{label} “{raw}” must be a positive amount with at most 2 decimals"))
        return None
    return value


def _int(raw: str, label: str, errors: list, line: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        return 0
    if not raw.isdigit():
        errors.append((line, f"{label} “{raw}” must be a whole number, 0 or more"))
        return 0
    return int(raw)


def _bool(raw: str, errors: list, line: int, default=True) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return default
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    errors.append((line, f"is_active “{raw}” must be yes or no"))
    return default


def parse(data: bytes, *, mode: str = "upsert", create_missing: bool = True, bank: image_import.ImageBank | None = None) -> Plan:
    """mode: 'upsert' (create + update by slug) or 'create' (skip products that already exist)."""
    plan = Plan()
    if len(data) > MAX_BYTES:
        plan.errors.append((0, f"File is larger than {MAX_BYTES // 1000} KB"))
        return plan
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        plan.errors.append((0, "File must be UTF-8 encoded CSV (in Excel: Save as → CSV UTF-8)"))
        return plan
    reader = csv.DictReader(io.StringIO(text))
    headers = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
    missing = REQUIRED - set(headers)
    if missing:
        plan.errors.append((1, f"Missing required column(s): {', '.join(sorted(missing))}"))
        return plan
    unknown = [h for h in headers if h and h not in COLUMNS]
    if unknown:
        plan.errors.append((1, f"Unknown column(s): {', '.join(unknown)} — use the demo CSV headers"))
        return plan

    categories = {c.slug: c for c in Category.objects.all()} | {c.name.lower(): c for c in Category.objects.all()}
    colors = {c.name.lower() for c in Color.objects.all()}
    sizes = {s.name.lower() for s in Size.objects.all()}
    existing_products = {p.slug: p for p in Product.objects.all()}
    existing_skus = {p.sku: p.slug for p in Product.objects.all()}
    existing_variant_skus = dict(ProductVariant.objects.values_list("sku", "product__slug"))

    specs: dict[str, ProductSpec] = {}
    seen_variant_skus: dict[str, str] = {}
    seen_product_skus: dict[str, str] = {}
    n = 0
    for line, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        n += 1
        if n > MAX_ROWS:
            plan.errors.append((line, f"More than {MAX_ROWS} rows — split the file"))
            break
        name, slug = row.get("name", ""), row.get("slug", "")
        key = slugify(slug or name)
        if slug and len(key) > SLUG_MAX:
            plan.errors.append((line, f"slug “{slug[:60]}” is longer than {SLUG_MAX} characters — shorten it (or leave the slug column empty and it is made from the name)"))
            continue
        if not key:
            plan.errors.append((line, "name (or slug) is required to group rows into a product"))
            continue
        spec = specs.get(key)
        first = spec is None
        if first:
            spec = specs[key] = ProductSpec(key=key, line=line, slug=_fit_slug(key))
            spec.exists = spec.slug in existing_products
        # --- product-level fields (first row wins; later rows may repeat or leave blank) ---
        fields = {"name": name, "sku": row.get("sku", ""), "category": row.get("category", ""), "description": row.get("description", ""), "badge": row.get("badge", "")}
        for fname, value in fields.items():
            if not value:
                continue
            current = getattr(spec, fname)
            if first or not current:
                setattr(spec, fname, value)
            elif current != value:
                plan.errors.append((line, f"{fname} “{value}” conflicts with “{current}” on the product's first row"))
        price = _decimal(row.get("price", ""), "price", plan.errors, line, required=first)
        if price is not None:
            if not first and spec.price and price != spec.price:
                plan.errors.append((line, f"price {price} conflicts with {spec.price} on the product's first row (use variant_price for per-variant prices)"))
            spec.price = price
        old = _decimal(row.get("old_price", ""), "old_price", plan.errors, line)
        if old is not None:
            spec.old_price = old
        if row.get("stock") and first:
            spec.stock = _int(row["stock"], "stock", plan.errors, line)
        if first:
            spec.is_active = _bool(row.get("is_active", ""), plan.errors, line)
        # --- photos: filenames (inside the ZIP) and/or public links, separated by | ---
        for ref in image_import.split_refs(row.get("images", "")):
            if ref.lower() not in {r.lower() for r in spec.images}:
                spec.images.append(ref)
        color, size = row.get("color", ""), row.get("size", "")
        color_refs = image_import.split_refs(row.get("color_images", ""))
        if color_refs and not color:
            plan.errors.append((line, "color_images needs a color on the same row"))
        elif color_refs:
            bucket = next((k for k in spec.color_images if k.lower() == color.lower()), color)
            have = spec.color_images.setdefault(bucket, [])
            have.extend(r for r in color_refs if r.lower() not in {h.lower() for h in have})
        # --- variant row ---
        if not (color or size):
            if spec.variants:
                plan.errors.append((line, "a row without color/size can't be mixed with variant rows of the same product"))
            continue
        if len(color) > 40 or len(size) > 20:
            plan.errors.append((line, "color is limited to 40 characters and size to 20"))
        color_hex = row.get("color_hex", "")
        if color_hex and not HEX.match(color_hex):
            plan.errors.append((line, f"color_hex “{color_hex}” must look like #1c3129"))
            color_hex = ""
        vprice = _decimal(row.get("variant_price", ""), "variant_price", plan.errors, line)
        vstock = _int(row.get("variant_stock", ""), "variant_stock", plan.errors, line)
        vsku = row.get("variant_sku", "")
        if len(vsku) > 60:
            plan.errors.append((line, "variant_sku is limited to 60 characters"))
        variant = VariantSpec(color=color, color_hex=color_hex, size=size, sku=vsku, price=vprice, stock=vstock)
        if any(v.color.lower() == color.lower() and v.size.lower() == size.lower() for v in spec.variants):
            plan.errors.append((line, f"duplicate variant {color or '-'} / {size or '-'} for “{spec.name or key}”"))
        spec.variants.append(variant)
        if vsku:
            owner = seen_variant_skus.get(vsku.lower())
            if owner and owner != key:
                plan.errors.append((line, f"variant_sku “{vsku}” is used by another product in this file"))
            seen_variant_skus[vsku.lower()] = key
            db_owner = existing_variant_skus.get(vsku)
            if db_owner and db_owner != spec.slug:
                plan.errors.append((line, f"variant_sku “{vsku}” already belongs to product “{db_owner}”"))
        if create_missing:
            if color and color.lower() not in colors:
                plan.new_colors.add(color)
            if size and size.lower() not in sizes:
                plan.new_sizes.add(size)
        else:
            if color and color.lower() not in colors:
                plan.errors.append((line, f"color “{color}” doesn't exist — add it under Colors or tick “create missing”"))
            if size and size.lower() not in sizes:
                plan.errors.append((line, f"size “{size}” doesn't exist — add it under Sizes or tick “create missing”"))

    # --- product-level validation after grouping ---
    for key, spec in specs.items():
        if mode == "create" and spec.exists:
            plan.skipped.append(spec.name or key)
            continue
        existing = existing_products.get(spec.slug)
        if existing:
            spec.name = spec.name or existing.name
            spec.sku = spec.sku or existing.sku
            spec.category = spec.category or existing.category.name
        if not spec.name:
            plan.errors.append((spec.line, f"product “{key}” has no name"))
        if len(spec.name) > NAME_MAX or len(spec.badge) > 30:
            plan.errors.append((spec.line, f"“{spec.name[:30]}”: name is limited to {NAME_MAX} characters and badge to 30"))
        if not spec.category:
            plan.errors.append((spec.line, f"“{spec.name or key}”: category is required"))
        else:
            cat = categories.get(spec.category.lower()) or categories.get(slugify(spec.category))
            if cat is None and len(spec.category) > CATEGORY_NAME_MAX:
                plan.errors.append((spec.line, f"category “{spec.category[:60]}…” is too long — use {CATEGORY_NAME_MAX} characters or fewer"))
            elif cat is None:
                if create_missing:
                    plan.new_categories.add(spec.category)
                else:
                    plan.errors.append((spec.line, f"category “{spec.category}” doesn't exist — create it first or tick “create missing”"))
        if spec.sku:
            if len(spec.sku) > 40:
                plan.errors.append((spec.line, f"sku “{spec.sku}” is limited to 40 characters"))
            owner = existing_skus.get(spec.sku)
            if owner and owner != spec.slug:
                plan.errors.append((spec.line, f"sku “{spec.sku}” already belongs to product “{owner}”"))
            other = seen_product_skus.get(spec.sku.lower())
            if other and other != key:
                plan.errors.append((spec.line, f"sku “{spec.sku}” is used twice in this file"))
            seen_product_skus[spec.sku.lower()] = key
        plan.products.append(spec)
    _check_images(plan, bank)
    if not plan.products and not plan.errors:
        plan.errors.append((0, "No products found in the file" if not plan.skipped else "Every product already exists (create-only mode)"))
    return plan


def _sort_refs(plan: Plan, spec: ProductSpec, label: str, refs: list[str], bank, counts: dict) -> tuple[list[str], list[str]]:
    """Split one cell's photos into (attach now, wait for a later upload) and flag anything that is simply wrong."""
    active, waiting = [], []
    for ref in refs:
        counts["refs"] += 1
        if image_import.is_url(ref):
            counts["urls"] += 1
            host = ref.split("/")[2] if ref.count("/") >= 2 else ""
            if not host or " " in ref:
                plan.errors.append((spec.line, f"{label}: “{ref[:80]}” is not a valid link"))
            else:
                active.append(ref)
            continue
        if image_import.Path(ref).suffix.lower() not in image_import.PHOTO_EXTS:
            plan.errors.append((spec.line, f"{label}: “{ref[:80]}” must end in .jpg, .jpeg, .png or .webp (or be a full http(s) link)"))
        elif bank is None or not bank.path:
            waiting.append(ref)
        elif bank.error:
            if not counts["zip_error"]:
                plan.errors.append((0, bank.error))
                counts["zip_error"] = True
        else:
            try:
                found = bank.lookup(ref)
            except image_import.ImportImageError as exc:
                plan.errors.append((spec.line, f"{label}: {exc}"))
                continue
            (active if found is not None else waiting).append(ref)
    return active, waiting


def _check_images(plan: Plan, bank: image_import.ImageBank | None) -> None:
    """Cheap checks only (links look right, limits); the photos themselves are verified by the background job.

    A file name that is not in the uploaded ZIP (or when no ZIP is given) is not an error: the name is saved with the product
    and the photo is attached automatically when a ZIP containing it is uploaded later."""
    counts = {"refs": 0, "urls": 0, "zip_error": False}
    limit = image_import.max_per_product()
    for spec in plan.products:
        if sum(len(r) for r in [spec.images, *spec.color_images.values()]) > limit:
            plan.errors.append((spec.line, f"“{spec.name or spec.slug}” has more than {limit} photos (change the limit in Site settings → Photo limits)"))
        spec.images, spec.pending_images = _sort_refs(plan, spec, "images", spec.images, bank, counts)
        for color, refs in list(spec.color_images.items()):
            active, waiting = _sort_refs(plan, spec, f"color_images ({color})", refs, bank, counts)
            spec.color_images[color] = active
            if waiting:
                spec.pending_color_images[color] = waiting
        spec.color_images = {c: r for c, r in spec.color_images.items() if r}
    if counts["refs"] > image_import.MAX_REFS:
        plan.errors.append((0, f"More than {image_import.MAX_REFS} photos in one import: split the CSV and ZIP into batches"))
    if counts["urls"] > image_import.MAX_URLS:
        plan.errors.append((0, f"More than {image_import.MAX_URLS} photo links in one import: put the photos in the ZIP instead, or split the file"))


@transaction.atomic
def apply(plan: Plan) -> dict:
    """Write a fully valid plan. Re-queries lookups so concurrent admin edits are respected."""
    if not plan.ok:
        raise ValueError("Cannot apply a plan with errors")
    created = updated = variants_created = variants_updated = 0
    next_number = Product.objects.count() + 1000
    for spec in plan.products:
        category = (
            Category.objects.filter(slug=slugify(spec.category)).first()
            or Category.objects.filter(name__iexact=spec.category).first()
        )
        if category is None:
            category = Category.objects.create(name=spec.category, title=spec.category, slug=slugify(spec.category))
        product = Product.objects.filter(slug=spec.slug).first()
        values = {
            "name": spec.name, "category": category, "description": spec.description or (product.description if product else ""),
            "price": spec.price, "old_price": spec.old_price, "badge": spec.badge or (product.badge if product else ""),
            "is_active": spec.is_active,
        }
        if not spec.variants:
            values["stock"] = spec.stock
        if product is None:
            sku = spec.sku
            while not sku or Product.objects.filter(sku=sku).exists():
                next_number += 1
                sku = f"NM-{next_number}"
            product = Product.objects.create(slug=spec.slug, sku=sku, art_type="bag", **values)
            created += 1
        else:
            for attr, value in values.items():
                setattr(product, attr, value)
            if spec.sku:
                product.sku = spec.sku
            product.save()
            updated += 1
        for v in spec.variants:
            color = size = None
            if v.color:
                color = Color.objects.filter(name__iexact=v.color).first() or Color.objects.create(name=v.color, hex_code=v.color_hex or "#cccccc")
                if v.color_hex and color.hex_code != v.color_hex:
                    color.hex_code = v.color_hex
                    color.save(update_fields=["hex_code"])
            if v.size:
                size = Size.objects.filter(name__iexact=v.size).first() or Size.objects.create(name=v.size, position=Size.objects.count() + 1)
            variant = (ProductVariant.objects.filter(sku=v.sku).first() if v.sku else None) or ProductVariant.objects.filter(
                product=product, color=color, size=size
            ).first()
            if variant is None:
                variant = ProductVariant(product=product, color=color, size=size)
                variants_created += 1
            else:
                variants_updated += 1
            variant.product, variant.color, variant.size = product, color, size
            variant.price, variant.stock, variant.is_active = v.price, v.stock, True
            if v.sku:
                variant.sku = v.sku
            variant.save()
        if spec.images or spec.color_images or spec.pending_images or spec.pending_color_images:
            # names whose files are not uploaded yet wait with the product; a later ZIP attaches them by file name
            PendingPhoto.objects.filter(product=product).delete()
            position = 0
            for ref in spec.pending_images:
                PendingPhoto.objects.create(product=product, ref=ref, position=position)
                position += 1
            for color_name, refs in spec.pending_color_images.items():
                color_obj = Color.objects.filter(name__iexact=color_name).first()
                for ref in refs:
                    PendingPhoto.objects.create(product=product, color=color_obj, ref=ref, position=position)
                    position += 1
    return {"created": created, "updated": updated, "variants_created": variants_created, "variants_updated": variants_updated}
