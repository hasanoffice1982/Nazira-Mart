from django.core.paginator import Paginator
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, TemplateView

from apps.core.models import Benefit, HeroSlide, PromoBanner, SiteSettings

from .models import Product
from apps.core.tracking import product_event

from .popular import popular_products
from apps.core.cached import cached

from .services import COLLECTIONS, active_categories, card_prefetch, search_products


class ProductListMixin:
    page_size = 12

    def product_context(self) -> dict:
        params = self.request.GET
        category = params.get("category", "")
        query = params.get("q", "").strip()
        sort = params.get("sort", "")
        collection = params.get("collection", "")
        if collection not in COLLECTIONS:
            collection = "new" if sort == "new" else ""   # older links used ?sort=new
        page = Paginator(search_products(category=category, query=query, sort=sort, collection=collection), self.page_size)
        return {
            "products": page.get_page(params.get("page")),
            "categories": active_categories(),
            "active_category": category,
            "query": query,
            "collection": collection,
            "collection_title": COLLECTIONS.get(collection, ""),
            "sort": sort,
        }


class HomeView(ProductListMixin, TemplateView):
    template_name = "catalog/home.html"
    page_size = 8

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.product_context())
        site = SiteSettings.load()
        ctx["hero_slides"] = cached("home_hero_slides", lambda: list(HeroSlide.objects.filter(is_active=True).select_related("product__category").prefetch_related("product__images")[:10]))
        ctx["promo_banners"] = cached("home_promo_banners", lambda: list(PromoBanner.objects.filter(is_active=True)[:10]))
        ctx["popular_products"] = popular_products(site)
        ctx["popular_cols"] = {5: 5, 6: 3, 9: 3, 12: 4}.get(len(ctx["popular_products"]), 4)
        ctx["benefits"] = cached("home_benefits", lambda: list(Benefit.objects.filter(is_active=True)))
        return ctx


class ShopView(ProductListMixin, TemplateView):
    template_name = "catalog/shop.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.product_context())
        return ctx


class ProductFragmentView(ProductListMixin, TemplateView):
    """HTMX target: filter bar + grid."""

    template_name = "catalog/_products_section.html"
    page_size = 8

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.product_context())
        ctx["fragment_url"] = self.request.path
        return ctx


class ProductDetailView(DetailView):
    template_name = "catalog/detail.html"
    queryset = Product.objects.filter(is_active=True).select_related("category").prefetch_related(*card_prefetch())
    context_object_name = "product"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["track_data"] = product_event("view_item", self.object, SiteSettings.load().currency_code)
        return ctx


class QuickViewView(TemplateView):
    template_name = "catalog/_quick_view.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["product"] = get_object_or_404(
            Product.objects.select_related("category").prefetch_related(*card_prefetch()), slug=kwargs["slug"], is_active=True
        )
        return ctx


@method_decorator(ratelimit(key="ip", rate="120/m", block=True), name="dispatch")
class SearchSuggestView(TemplateView):
    """Live search dropdown (HTMX)."""

    template_name = "catalog/_suggest.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        q = self.request.GET.get("q", "").strip()
        ctx["query"] = q
        ctx["results"] = search_products(query=q)[:6] if len(q) >= 2 else []
        return ctx
