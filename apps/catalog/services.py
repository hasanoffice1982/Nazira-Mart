from django.db.models import F, Prefetch, Q, QuerySet

from apps.core.cached import cached

from .models import Category, Product, ProductVariant

SORTS = {
    "new": "-created_at",
    "price_asc": "price",
    "price_desc": "-price",
    "rating": "-rating",
}


COLLECTIONS = {
    "new": "New arrivals",
    "gifts": "Gift ideas",
    "sale": "Discounted products",
}


def card_prefetch() -> list:
    """What a product card or page needs, in two queries: all variants WITH their colour and size (one JOIN), and the photos."""
    return [Prefetch("variants", queryset=ProductVariant.objects.select_related("color", "size")), "images"]


def search_products(*, category: str = "", query: str = "", sort: str = "", collection: str = "") -> QuerySet[Product]:
    qs = Product.objects.filter(is_active=True).select_related("category").prefetch_related(*card_prefetch())
    if category:
        qs = qs.filter(category__slug=category)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(category__name__icontains=query) | Q(sku__iexact=query))
    if collection == "gifts":
        qs = qs.filter(is_gift=True)
    elif collection == "sale":
        qs = qs.filter(old_price__isnull=False, old_price__gt=F("price"))
    elif collection == "new" and not sort:
        sort = "new"
    return qs.order_by(SORTS.get(sort, "id"))


def active_categories() -> list[Category]:
    return cached("active_categories", lambda: list(Category.objects.filter(is_active=True)))
