from django.urls import path

from . import views

app_name = "orders"
urlpatterns = [
    path("", views.OrderListView.as_view(), name="list"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("checkout/coupon/", views.ApplyCouponView.as_view(), name="coupon_apply"),
    path("checkout/coupon/remove/", views.RemoveCouponView.as_view(), name="coupon_remove"),
    path("checkout/confirm/", views.ConfirmCheckoutView.as_view(), name="confirm"),
    path("checkout/confirm/resend/", views.ResendCodeView.as_view(), name="resend"),
    path("<str:number>/", views.OrderDetailView.as_view(), name="detail"),
    path("<str:number>/placed/", views.OrderSuccessView.as_view(), name="success"),
    path("<str:number>/cancel/", views.CancelOrderView.as_view(), name="cancel"),
]
