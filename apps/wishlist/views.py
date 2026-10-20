from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.catalog.models import Product, ProductVariant
from apps.core.htmx import hx_trigger

from .models import WishlistItem


class WishlistView(LoginRequiredMixin, ListView):
    template_name = "wishlist/list.html"
    context_object_name = "items"

    def get_queryset(self):
        return WishlistItem.objects.filter(user=self.request.user, product__is_active=True).select_related("product__category").prefetch_related(Prefetch("product__variants", queryset=ProductVariant.objects.select_related("color", "size")), "product__images")


class ToggleWishlistView(View):
    def post(self, request, product_id: int):
        if not request.user.is_authenticated:
            resp = HttpResponse(status=204)
            resp["HX-Redirect"] = f"{reverse('accounts:login')}?next={request.META.get('HTTP_HX_CURRENT_URL', '/')}"
            return resp
        product = get_object_or_404(Product, pk=product_id, is_active=True)
        item, created = WishlistItem.objects.get_or_create(user=request.user, product=product)
        if not created:
            item.delete()
        resp = render(request, "wishlist/_button.html", {"product": product, "liked": created})
        return hx_trigger(resp, toast="Added to your wishlist ♡" if created else "Removed from wishlist")
