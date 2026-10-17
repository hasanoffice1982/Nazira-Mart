from django.urls import path

from . import views

app_name = "catalog"
urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("search/suggest/", views.SearchSuggestView.as_view(), name="suggest"),
    path("shop/", views.ShopView.as_view(), name="shop"),
    path("shop/fragment/", views.ProductFragmentView.as_view(), name="fragment"),
    path("product/<slug:slug>/", views.ProductDetailView.as_view(), name="detail"),
    path("product/<slug:slug>/quick/", views.QuickViewView.as_view(), name="quick"),
]
