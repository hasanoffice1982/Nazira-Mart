from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.SignInView.as_view(), name="login"),
    path("logout/", views.SignOutView.as_view(), name="logout"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("", views.AccountView.as_view(), name="account"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("addresses/", views.AddressBookView.as_view(), name="addresses"),
    path("addresses/present/", views.PresentAddressView.as_view(), name="address_present"),
    path("addresses/shipping/", views.ShippingAddressesView.as_view(), name="address_shipping"),
    path("addresses/shipping/new/", views.ShippingAddressFormView.as_view(), name="address_new"),
    path("addresses/shipping/<int:pk>/edit/", views.ShippingAddressFormView.as_view(), name="address_edit"),
    path("addresses/shipping/<int:pk>/default/", views.ShippingAddressActionView.as_view(action="default"), name="address_default"),
    path("addresses/shipping/<int:pk>/delete/", views.ShippingAddressActionView.as_view(action="delete"), name="address_delete"),
    path("2fa/", views.TwoFactorChallengeView.as_view(), name="two_factor"),
    path("2fa/resend/", views.TwoFactorResendView.as_view(), name="two_factor_resend"),
    path("security/", views.SecurityView.as_view(), name="security"),
    path("security/enable/", views.EnableTwoFactorView.as_view(), name="two_factor_enable"),
    path("security/disable/", views.DisableTwoFactorView.as_view(), name="two_factor_disable"),
    path("verify/resend/", views.ResendVerificationView.as_view(), name="verify_resend"),
    path("verify/<str:token>/", views.VerifyEmailView.as_view(), name="verify"),
    path("password/change/", views.ChangePasswordView.as_view(), name="password_change"),
    path("password/reset/", views.ResetRequestView.as_view(), name="password_reset"),
    path("password/reset/sent/", views.ResetDoneView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", views.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", views.ResetCompleteView.as_view(), name="password_reset_complete"),
]
