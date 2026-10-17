from django.db.models import Q, QuerySet

from .models import Category, Product

SORTS = {
    "new": "-created_at",
    "price_asc": "price",
    "price_desc": "-price",
    "rating": "-rating",
}


def search_products(*, category: str = "", query: str = "", sort: str = "") -> QuerySet[Product]:
    qs = Product.objects.filter(is_active=True).select_related("category").prefetch_related("variants__color", "variants__size", "images")
    if category:
        qs = qs.filter(category__slug=category)
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(category__name__icontains=query) | Q(sku__iexact=query))
    return qs.order_by(SORTS.get(sort, "id"))


def active_categories() -> QuerySet[Category]:
    return Category.objects.filter(is_active=True)
