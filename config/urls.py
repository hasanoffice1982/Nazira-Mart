from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

handler403 = "apps.core.views.forbidden"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("wishlist/", include("apps.wishlist.urls")),
    path("cart/", include("apps.cart.urls")),
    path("orders/", include("apps.orders.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.core.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
