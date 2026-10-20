from apps.cart.services import get_cart
from apps.catalog.services import active_categories
from apps.orders.promotions import COUPON_SESSION_KEY, evaluate_for_request
from apps.wishlist.models import WishlistItem

from .cached import cached
from .models import Page, SiteSettings


def storefront(request) -> dict:
    site = SiteSettings.load()
    categories = list(active_categories())
    cart = get_cart(request)
    lines = cart.lines()
    wishlist_ids: set[int] = set()
    if request.user.is_authenticated:
        wishlist_ids = set(WishlistItem.objects.filter(user=request.user).values_list("product_id", flat=True))
    subtotal = sum(l.total for l in lines)
    deal = evaluate_for_request(request, subtotal, request.session.get(COUPON_SESSION_KEY, "")) if lines else None
    return {
        "cart_deal": deal,
        "site": site,
        "footer_pages": cached("footer_pages", lambda: list(Page.objects.filter(is_published=True).exclude(group=Page.Group.NONE).only("slug", "title", "group"))),
        "cart_lines": lines,
        "cart_count": sum(l.quantity for l in lines),
        "cart_subtotal": subtotal,
        "wishlist_ids": wishlist_ids,
        "wishlist_count": len(wishlist_ids),
        "nav_categories": categories,
        "header_categories": [c for c in categories if c.show_in_nav][: site.nav_max_categories],
        "header_menu_categories": [c for c in categories if c.show_in_nav],   # the Shop All dropdown lists every one of them
        "currency": site.currency_symbol,
    }
