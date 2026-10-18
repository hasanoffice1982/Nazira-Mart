import uuid
from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from django.urls import reverse

from apps.core.richtext import sanitize_field


MAX_IMAGE_BYTES = 5 * 1024 * 1024   # per photo; every upload is shrunk to ~150 KB on save
MAX_IMAGE_PIXELS = 60_000_000       # JPEGs are decoded at reduced size (see core.images), so large photos stay cheap
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_photo(file) -> None:
    """Reject oversized files, non-photos and unusual formats (extension alone is never trusted)."""
    from PIL import Image

    if file.size > MAX_IMAGE_BYTES:
        raise ValidationError(f"Photo is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(file) as img:
            fmt, (w, h) = img.format, img.size
            img.verify()
    except Exception:  # noqa: BLE001
        raise ValidationError("This file isn't a valid image.")
    finally:
        file.seek(0)
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError("Use JPEG, PNG or WebP photos.")
    if w * h > MAX_IMAGE_PIXELS:
        raise ValidationError("Image dimensions are too large (max ~40 megapixels).")


class Category(models.Model):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=255, unique=True)
    title = models.CharField(max_length=80, help_text="Display title on home tiles")
    description = models.CharField(max_length=120, blank=True)
    art_type = models.CharField(max_length=30, blank=True)
    image = models.ImageField(upload_to="categories/", blank=True)
    css_class = models.CharField(max_length=30, blank=True)
    position = models.PositiveSmallIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True)
    show_in_nav = models.BooleanField("Show in header menu", default=True, help_text="Untick to keep the category on the site but out of the top menu")

    class Meta:
        ordering = ("position", "name")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


DEFAULT_PRODUCT_BG = "#f4f7f3"
# Product picture background follows the category unless a product sets its own color (Media & look > Custom background)
CATEGORY_TINTS = {
    "women": "#dcf0f3", "bags": "#ffeedd", "shoes": "#fbe9d8", "beauty": "#e6f2f7",
    "jewellery": "#fff6d9", "toys": "#fff1cc", "kids": "#e1f4ff",
}
FALLBACK_TINTS = ("#dcf0f3", "#ffeedd", "#e1f4ff", "#fff6d9", "#e6f2f7", "#fbe9d8")


class Product(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    sku = models.CharField(max_length=40, unique=True)
    description = models.TextField(blank=True)
    art_type = models.CharField(max_length=30, help_text="Illustration key used by the storefront")
    image = models.ImageField(upload_to="products/", blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    old_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    badge = models.CharField(max_length=30, blank=True)
    rating = models.DecimalField(max_digits=2, decimal_places=1, default=Decimal("4.5"))
    bg_color = models.CharField(max_length=9, default="#f4f7f3")
    swatches = models.JSONField(default=list, blank=True)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_gift = models.BooleanField("Gift idea", default=False, help_text="Shows this product in Shop All > Gift ideas.")
    is_popular_pick = models.BooleanField("Pin to Popular products", default=False, help_text="Shows this product in the home page Popular products section (see Site settings for how pins and best sellers mix)")
    popular_order = models.PositiveSmallIntegerField("Pin order", default=100, help_text="Lower numbers come first among pinned products")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "id")
        indexes = [models.Index(fields=["is_active", "category"])]

    @property
    def card_bg(self) -> str:
        """Background color behind the product picture: its own color if set, otherwise its category's soft tint."""
        if self.bg_color and self.bg_color.lower() != DEFAULT_PRODUCT_BG:
            return self.bg_color
        category = self.category
        return CATEGORY_TINTS.get(category.slug) or FALLBACK_TINTS[category.pk % len(FALLBACK_TINTS)]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.description = sanitize_field(self.description)
        super().save(*args, **kwargs)
        cache.delete("popular_ids")

    def get_absolute_url(self) -> str:
        return reverse("catalog:detail", args=[self.slug])

    # --- variants -------------------------------------------------------------
    @property
    def active_variants(self) -> list["ProductVariant"]:
        """Uses the prefetch cache when available (`prefetch_related("variants")`) to avoid N+1 queries."""
        return [v for v in self.variants.all() if v.is_active]

    @property
    def has_variants(self) -> bool:
        return bool(self.active_variants)

    @property
    def total_stock(self) -> int:
        variants = self.active_variants
        return sum(v.stock for v in variants) if variants else self.stock

    @property
    def in_stock(self) -> bool:
        return self.total_stock > 0

    @property
    def from_price(self):
        prices = [v.unit_price for v in self.active_variants] or [self.price]
        return min(prices)

    @property
    def price_varies(self) -> bool:
        return len({v.unit_price for v in self.active_variants}) > 1

    # --- photo gallery ---------------------------------------------------------
    @property
    def gallery_images(self) -> list["ProductImage"]:
        """All gallery photos in display order (uses the prefetch cache when available)."""
        return sorted(self.images.all(), key=lambda i: (i.position, i.pk))

    @property
    def shared_images(self) -> list["ProductImage"]:
        return [i for i in self.gallery_images if i.color_id is None]

    @property
    def cover_image(self):
        """Photo for cards and search: first shared gallery photo, else any gallery photo, else the legacy single image."""
        gallery = self.gallery_images
        if gallery:
            return (next((i for i in gallery if i.color_id is None), gallery[0])).image
        return self.image or None

    def images_for_color(self, color_id=None) -> list["ProductImage"]:
        """Colour-specific photos first, then the shared ones. With no colour chosen: shared, or everything if none are shared."""
        gallery = self.gallery_images
        shared = [i for i in gallery if i.color_id is None]
        if not color_id:
            return shared or gallery
        return [i for i in gallery if i.color_id == int(color_id)] + shared

    @property
    def colors(self) -> list["Color"]:
        seen: dict[int, Color] = {}
        for v in self.active_variants:
            if v.color_id and v.color_id not in seen:
                seen[v.color_id] = v.color
        return sorted(seen.values(), key=lambda c: (c.position, c.name))

    @property
    def sizes(self) -> list["Size"]:
        seen: dict[int, Size] = {}
        for v in self.active_variants:
            if v.size_id and v.size_id not in seen:
                seen[v.size_id] = v.size
        return sorted(seen.values(), key=lambda s: (s.position, s.name))

    def find_variant(self, color_id=None, size_id=None) -> "ProductVariant | None":
        color_id, size_id = int(color_id) if color_id else None, int(size_id) if size_id else None
        for v in self.active_variants:
            if v.color_id == color_id and v.size_id == size_id:
                return v
        return None

    @property
    def badge_is_discount(self) -> bool:
        return self.badge.startswith("-")


class Color(models.Model):
    name = models.CharField(max_length=40, unique=True)
    hex_code = models.CharField("swatch colour", max_length=7, default="#cccccc", help_text="e.g. #1c3129")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "name")

    def __str__(self) -> str:
        return self.name


class Size(models.Model):
    name = models.CharField(max_length=20, unique=True, help_text="e.g. S, M, L, XL, 42")
    position = models.PositiveSmallIntegerField(default=0, help_text="Lower numbers are shown first")

    class Meta:
        ordering = ("position", "name")

    def __str__(self) -> str:
        return self.name


class ProductVariant(models.Model):
    """One sellable combination of colour and/or size, with its own SKU, stock and optional price."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    color = models.ForeignKey(Color, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    size = models.ForeignKey(Size, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    sku = models.CharField(max_length=60, unique=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Leave empty to use the product price")
    stock = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to="variants/", blank=True)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "color__position", "size__position", "id")
        constraints = [
            models.CheckConstraint(condition=Q(color__isnull=False) | Q(size__isnull=False), name="variant_has_color_or_size"),
            models.UniqueConstraint(fields=["product", "color", "size"], name="uniq_variant_color_size"),
            models.UniqueConstraint(fields=["product", "color"], condition=Q(size__isnull=True), name="uniq_variant_color_only"),
            models.UniqueConstraint(fields=["product", "size"], condition=Q(color__isnull=True), name="uniq_variant_size_only"),
        ]

    def __str__(self) -> str:
        return f"{self.product.name} — {self.label}"

    @property
    def label(self) -> str:
        return " / ".join(x.name for x in (self.color, self.size) if x)

    @property
    def unit_price(self):
        return self.price if self.price is not None else self.product.price

    def build_sku(self) -> str:
        parts = [self.product.sku] + [slugify(x.name).upper() for x in (self.color, self.size) if x]
        return "-".join(parts)[:60]

    def save(self, *args, **kwargs):
        if not self.sku:
            self.sku = self.build_sku()
        super().save(*args, **kwargs)


class ProductImage(models.Model):
    """A gallery photo. No colour = shown for every variant; with a colour = shown only when that colour is chosen."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/gallery/", validators=[validate_photo])
    color = models.ForeignKey(Color, on_delete=models.CASCADE, null=True, blank=True, related_name="+", help_text="Leave empty to show this photo for every color")
    alt_text = models.CharField(max_length=160, blank=True)
    position = models.PositiveSmallIntegerField(default=0, help_text="Lower numbers come first")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return f"{self.product.name} photo #{self.pk}" + (f" ({self.color.name})" if self.color_id else "")


class ImageImportJob(models.Model):
    """Background photo import started after a CSV import (see image_import.py)."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Finished"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.QUEUED)
    plan = models.JSONField(default=list)  # [{"slug", "shared": [refs], "colors": {name: [refs]}}]
    zip_path = models.TextField(blank=True)  # one or more stored ZIP paths, one per line
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Photo import {self.created_at:%d %b %H:%M} ({self.status})"


def pending_key(ref: str) -> str:
    """How a photo file name is matched later: the file name without folders, lower case."""
    return ref.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


class PendingPhoto(models.Model):
    """A photo file name listed in a CSV import whose file has not been uploaded yet.

    When a ZIP containing that file name is uploaded (Admin -> Products -> Upload photos), the photo is attached to the product automatically."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="pending_photos")
    color = models.ForeignKey(Color, on_delete=models.CASCADE, null=True, blank=True, related_name="+", help_text="Empty = shown for every color")
    ref = models.CharField("File name", max_length=255)
    key = models.CharField(max_length=255, db_index=True, editable=False)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "photo waiting for upload"

    def save(self, *args, **kwargs):
        self.key = pending_key(self.ref)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.ref
