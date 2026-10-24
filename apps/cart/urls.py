from django.urls import path

from . import views

app_name = "cart"
urlpatterns = [
    path("add/<int:product_id>/", views.AddToCartView.as_view(), name="add"),
    path("qty/<int:product_id>/", views.ChangeQtyView.as_view(), name="qty"),
    path("contact/", views.CaptureContactView.as_view(), name="contact"),
    path("remove/<int:product_id>/", views.RemoveFromCartView.as_view(), name="remove"),
]
