from django.db.models import F, Q, QuerySet

from .models import Category, Product

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


def search_products(*, category: str = "", query: str = "", sort: str = "", collection: str = "") -> QuerySet[Product]:
    qs = Product.objects.filter(is_active=True).select_related("category").prefetch_related("variants__color", "variants__size", "images")
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


def active_categories() -> QuerySet[Category]:
    return Category.objects.filter(is_active=True)
