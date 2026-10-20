from django.urls import path

from . import views

app_name = "chatorders"
urlpatterns = [
    path("products/", views.products, name="products"),
    path("orders/", views.create_order, name="create"),
    path("orders/status/", views.order_status, name="status"),
]
