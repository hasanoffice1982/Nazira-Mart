from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from apps.catalog.models import Product, ProductVariant
from apps.core.htmx import hx_trigger
from apps.core.models import SiteSettings
from apps.core.tracking import product_event

from .services import get_cart


def resolve_variant(product: Product, data) -> tuple[ProductVariant | None, str]:
    """Pick the variant a customer selected. Returns (variant, error). Plain products return (None, "")."""
    if not product.has_variants:
        return None, ""
    variant_id = data.get("variant")
    if variant_id:
        variant = next((v for v in product.active_variants if str(v.pk) == str(variant_id)), None)
    else:
        variant = product.find_variant(data.get("color"), data.get("size"))
    if variant is None:
        need = " and ".join(n for n, has in (("color", product.colors), ("size", product.sizes)) if has) or "options"
        return None, f"Please choose a {need} first" if data.get("color") is None and data.get("size") is None else "That combination isn't available"
    return variant, ""


@method_decorator(ratelimit(key="ip", rate="120/m", method="POST", block=True), name="dispatch")
class CartMutationView(View):
    """Shared HTMX response: re-render the drawer + OOB counters."""

    toast = ""

    def respond(self, request, message: str = ""):
        resp = render(request, "cart/_drawer_body.html", {"cart_oob": True})
        text = message or self.toast
        if text:
            hx_trigger(resp, toast=text)
        return resp


class AddToCartView(CartMutationView):
    toast = "Added to your bag ✓"

    def post(self, request, product_id: int):
        product = get_object_or_404(Product.objects.prefetch_related("variants__color", "variants__size", "images"), pk=product_id, is_active=True)
        variant, error = resolve_variant(product, request.POST)
        if error:
            return self.respond(request, error)
        stock = variant.stock if variant else product.stock
        if stock < 1:
            return self.respond(request, "Sorry, this item is out of stock")
        message = get_cart(request).change(product, variant, 1)
        resp = self.respond(request, message)
        events = {"track": product_event("add_to_cart", product, SiteSettings.load().currency_code, price=variant.unit_price if variant else product.price)}
        if request.POST.get("open"):
            events.update(toast=message or self.toast, openCart=True)
        else:
            events.update(toast=message or self.toast)
        return hx_trigger(resp, **events)


class ChangeQtyView(CartMutationView):
    def post(self, request, product_id: int):
        product = get_object_or_404(Product, pk=product_id)
        variant = None
        if request.POST.get("variant"):
            variant = get_object_or_404(ProductVariant, pk=request.POST["variant"], product=product)
        delta = 1 if request.POST.get("delta") == "1" else -1
        return self.respond(request, get_cart(request).change(product, variant, delta))


class RemoveFromCartView(CartMutationView):
    def post(self, request, product_id: int):
        get_cart(request).remove(product_id, int(request.POST.get("variant") or 0))
        return self.respond(request)
