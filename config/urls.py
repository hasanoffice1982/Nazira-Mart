from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.templatetags.static import static as static_url
from django.urls import include, path
from django.views.generic import RedirectView

handler403 = "apps.core.views.forbidden"

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url=static_url("favicon.ico"), permanent=True)),  # browsers ask for /favicon.ico by default
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("wishlist/", include("apps.wishlist.urls")),
    path("cart/", include("apps.cart.urls")),
    path("orders/", include("apps.orders.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.core.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
