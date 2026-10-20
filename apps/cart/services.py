"""Cart business logic. Guests use the session; signed-in customers use the database."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from apps.catalog.models import Product, ProductVariant
from apps.catalog.services import card_prefetch
from apps.core.models import SiteSettings

from .models import Cart, CartItem

SESSION_KEY = "cart"
MAX_QTY = 20
Key = tuple[int, int]  # (product_id, variant_id) — variant_id 0 means "no variant"


@dataclass
class Line:
    product: Product
    variant: ProductVariant | None
    quantity: int

    @property
    def unit_price(self) -> Decimal:
        return self.variant.unit_price if self.variant else self.product.price

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def label(self) -> str:
        return self.variant.label if self.variant else ""

    @property
    def image(self):
        """Variant photo, else a photo tagged with the variant's colour, else the product's cover photo."""
        if self.variant:
            if self.variant.image:
                return self.variant.image
            if self.variant.color_id:
                tagged = [i for i in self.product.gallery_images if i.color_id == self.variant.color_id]
                if tagged:
                    return tagged[0].image
        return self.product.cover_image

    @property
    def stock(self) -> int:
        return self.variant.stock if self.variant else self.product.stock


def delivery_charge(subtotal: Decimal, area: str = "dhaka") -> Decimal:
    """Delivery cost from Admin -> Site settings (read fresh, so a price change applies to the very next order)."""
    return SiteSettings.load_fresh().delivery_for(subtotal, area)


def _parse_session_key(key: str) -> Key:
    pid, _, vid = str(key).partition(":")  # also accepts the old "pid" format
    return int(pid), int(vid or 0)


class CartService:
    def __init__(self, request):
        self.request = request
        self.user = request.user if request.user.is_authenticated else None

    # --- storage -------------------------------------------------------
    def _raw(self) -> dict[Key, int]:
        if self.user:
            rows = CartItem.objects.filter(cart__user=self.user).order_by("id").values_list("product_id", "variant_id", "quantity")
            return {(pid, vid or 0): qty for pid, vid, qty in rows}
        return {_parse_session_key(k): v for k, v in self.request.session.get(SESSION_KEY, {}).items()}

    def _save_raw(self, data: dict[Key, int]) -> None:
        self.request._cart_lines = None   # the cart changed: the per-request copy below is stale
        if self.user:
            cart, _ = Cart.objects.get_or_create(user=self.user)
            for item in cart.items.all():
                if (item.product_id, item.variant_id or 0) not in data:
                    item.delete()
            for (pid, vid), qty in data.items():
                CartItem.objects.update_or_create(cart=cart, product_id=pid, variant_id=vid or None, defaults={"quantity": qty})
        else:
            self.request.session[SESSION_KEY] = {f"{pid}:{vid}": qty for (pid, vid), qty in data.items()}
            self.request.session.modified = True

    # --- queries -------------------------------------------------------
    def lines(self) -> list[Line]:
        """The cart's lines. Loaded once per request (the header, drawer, checkout and totals all ask for it) in three queries."""
        cached = getattr(self.request, "_cart_lines", None)
        if cached is not None and cached[0] == (self.user.pk if self.user else 0):
            return cached[1]
        out = self._load_lines()
        self.request._cart_lines = ((self.user.pk if self.user else 0), out)
        return out

    def _load_lines(self) -> list[Line]:
        raw = self._raw()
        if not raw:
            return []
        products = {p.pk: p for p in Product.objects.filter(pk__in={pid for pid, _ in raw}, is_active=True).select_related("category").prefetch_related(*card_prefetch())}
        variants = {v.pk: v for p in products.values() for v in p.variants.all() if v.is_active}   # already loaded with the products
        out = []
        for (pid, vid), qty in raw.items():
            product = products.get(pid)
            if product is None:
                continue
            variant = variants.get(vid) if vid else None
            if vid and (variant is None or variant.product_id != pid):
                continue  # variant removed/deactivated
            if not vid and product.has_variants:
                continue  # product gained variants; the plain entry is no longer sellable
            out.append(Line(product, variant, qty))
        return out

    @property
    def count(self) -> int:
        return sum(l.quantity for l in self.lines())

    @property
    def subtotal(self) -> Decimal:
        return sum((line.total for line in self.lines()), Decimal(0))

    # --- commands ------------------------------------------------------
    def change(self, product: Product, variant: ProductVariant | None, delta: int) -> str:
        key = (product.pk, variant.pk if variant else 0)
        data = self._raw()
        qty = data.get(key, 0) + delta
        stock = variant.stock if variant else product.stock
        message = ""
        if qty > stock:
            qty, message = stock, f"Only {stock} left in stock"
        qty = min(qty, MAX_QTY)
        if qty < 1:
            data.pop(key, None)
        else:
            data[key] = qty
        self._save_raw(data)
        return message

    def remove(self, product_id: int, variant_id: int = 0) -> None:
        data = self._raw()
        data.pop((product_id, variant_id or 0), None)
        self._save_raw(data)

    def clear(self) -> None:
        self._save_raw({})


def get_cart(request) -> CartService:
    return CartService(request)


def merge_session_cart(request, user) -> None:
    """Fold the guest session cart into the user's persistent cart after login."""
    guest = {_parse_session_key(k): v for k, v in request.session.get(SESSION_KEY, {}).items()}
    if not guest:
        return
    with transaction.atomic():
        cart, _ = Cart.objects.get_or_create(user=user)
        products = {p.pk: p for p in Product.objects.filter(pk__in={pid for pid, _ in guest}, is_active=True)}
        variants = {v.pk: v for v in ProductVariant.objects.filter(pk__in={vid for _, vid in guest if vid}, is_active=True)}
        for (pid, vid), qty in guest.items():
            product, variant = products.get(pid), variants.get(vid) if vid else None
            if product is None or (vid and (variant is None or variant.product_id != pid)):
                continue
            stock = variant.stock if variant else product.stock
            item, _ = CartItem.objects.get_or_create(cart=cart, product=product, variant=variant, defaults={"quantity": 0})
            item.quantity = min(item.quantity + qty, stock, MAX_QTY)
            if item.quantity > 0:
                item.save()
            else:
                item.delete()
    request.session.pop(SESSION_KEY, None)
