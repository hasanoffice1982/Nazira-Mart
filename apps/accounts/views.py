from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import (
    LoginView, LogoutView, PasswordChangeView, PasswordResetCompleteView, PasswordResetConfirmView,
    PasswordResetDoneView, PasswordResetView,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, FormView, TemplateView, UpdateView
from django_ratelimit.decorators import ratelimit

from apps.core.otp import OTPError, mask_email

from . import addresses, twofactor
from .forms import (
    DisableTwoFactorForm, EmailLoginForm, HoneypotPasswordResetForm, PresentAddressForm, ProfileForm, RegisterForm,
    ShippingAddressForm, TwoFactorCodeForm,
)
from .models import PresentAddress, ShippingAddress
from .services import (
    mark_email_verified, send_password_changed_email, send_verification_email, user_from_verify_token,
)


# Per-IP volume limits complement axes (which locks a specific username+IP pair after 5 failures).
@method_decorator(ratelimit(key="ip", rate="30/m", method="POST", block=True), name="post")
class SignInView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not form.cleaned_data.get("remember_me"):
            self.request.session.set_expiry(0)  # expires when the browser closes
        return response


class SignOutView(LogoutView):
    pass


@method_decorator(ratelimit(key="ip", rate="10/h", method="POST", block=True), name="post")
class RegisterView(CreateView):
    form_class = RegisterForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("accounts:account")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object, backend="django.contrib.auth.backends.ModelBackend")
        if send_verification_email(self.request, self.object):
            messages.success(self.request, f"Welcome! We sent a verification link to {self.object.email}.")
        else:
            messages.warning(self.request, "Account created, but we couldn't send the verification email. Use “Resend” below.")
        return response


class AccountView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/account.html"


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)


# ---- email verification ---------------------------------------------------
class VerifyEmailView(View):
    def get(self, request, token: str):
        user = user_from_verify_token(token)
        if user is None:
            return render(request, "accounts/verify_result.html", {"ok": False}, status=400)
        mark_email_verified(user)
        return render(request, "accounts/verify_result.html", {"ok": True})


@method_decorator(ratelimit(key="user_or_ip", rate="5/h", method="POST", block=True), name="post")
class ResendVerificationView(LoginRequiredMixin, View):
    def post(self, request):
        user = request.user
        if user.email_verified:
            messages.info(request, "Your email is already verified.")
        else:
            if send_verification_email(request, user):
                messages.success(request, f"Verification link sent to {user.email}.")
            else:
                messages.error(request, "We couldn't send the email right now. Please try again later.")
        return redirect("accounts:account")


# ---- password change / reset ---------------------------------------------
class ChangePasswordView(LoginRequiredMixin, PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:account")

    def form_valid(self, form):
        response = super().form_valid(form)  # keeps this session, invalidates all others
        send_password_changed_email(self.request, self.request.user)
        messages.success(self.request, "Password updated. Other devices have been signed out.")
        return response


@method_decorator(ratelimit(key="ip", rate="10/h", method="POST", block=True), name="post")
@method_decorator(ratelimit(key="post:email", rate="3/h", method="POST", block=True), name="post")
class ResetRequestView(PasswordResetView):
    form_class = HoneypotPasswordResetForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "accounts/email/password_reset.txt"
    html_email_template_name = "accounts/email/password_reset.html"
    subject_template_name = "accounts/email/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_email_context = {"brand": settings.SITE_NAME}


class ResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class ResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        mark_email_verified(self.user)  # clicking the emailed link proves ownership
        send_password_changed_email(self.request, self.user)
        return response


class ResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---- address book (HTMX partials) -----------------------------------------
class AddressBookMixin(LoginRequiredMixin):
    """Every query is scoped to request.user, so addresses are never visible across accounts."""

    def htmx_only(self, request):
        return None if request.headers.get("HX-Request") else redirect("accounts:addresses")

    def shipping_section(self, request, form=None, editing=None, status=200):
        ctx = {
            "addresses": request.user.shipping_addresses.all(), "form": form, "editing": editing,
            "can_add": addresses.can_add(request.user), "max_addresses": ShippingAddress.MAX_PER_USER,
        }
        return render(request, "accounts/addresses/_shipping_section.html", ctx, status=status)

    def present_box(self, request, form=None, status=200):
        present = PresentAddress.objects.filter(user=request.user).first()
        return render(request, "accounts/addresses/_present.html", {"present": present, "form": form}, status=status)


class AddressBookView(AddressBookMixin, TemplateView):
    template_name = "accounts/addresses/book.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx.update(
            present=PresentAddress.objects.filter(user=user).first(), form=None, editing=None,
            addresses=user.shipping_addresses.all(), can_add=addresses.can_add(user), max_addresses=ShippingAddress.MAX_PER_USER,
        )
        return ctx


@method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True), name="post")
class PresentAddressView(AddressBookMixin, View):
    def get(self, request):
        redir = self.htmx_only(request)
        if redir:
            return redir
        instance = PresentAddress.objects.filter(user=request.user).first()
        if request.GET.get("edit"):
            return self.present_box(request, form=PresentAddressForm(instance=instance))
        return self.present_box(request)

    def post(self, request):
        instance = PresentAddress.objects.filter(user=request.user).first()
        form = PresentAddressForm(request.POST, instance=instance)
        if not form.is_valid():
            return self.present_box(request, form=form)
        addresses.save_present(request.user, form.cleaned_data)
        return self.present_box(request)


class ShippingAddressesView(AddressBookMixin, View):
    def get(self, request):
        return self.htmx_only(request) or self.shipping_section(request)


@method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True), name="post")
class ShippingAddressFormView(AddressBookMixin, View):
    """Add (no pk) or edit (pk) a shipping address."""

    def get_instance(self, request, pk):
        return get_object_or_404(request.user.shipping_addresses, pk=pk) if pk else None

    def get(self, request, pk=None):
        redir = self.htmx_only(request)
        if redir:
            return redir
        instance = self.get_instance(request, pk)
        if instance is None and not addresses.can_add(request.user):
            return self.shipping_section(request)
        return self.shipping_section(request, form=ShippingAddressForm(instance=instance), editing=instance)

    def post(self, request, pk=None):
        instance = self.get_instance(request, pk)
        form = ShippingAddressForm(request.POST, instance=instance)
        if not form.is_valid():
            return self.shipping_section(request, form=form, editing=instance)
        try:
            addresses.save_shipping(request.user, form.save(commit=False), make_default=form.cleaned_data["make_default"])
        except addresses.AddressLimitError as exc:
            form.add_error(None, str(exc))
            return self.shipping_section(request, form=form, editing=instance)
        return self.shipping_section(request)


@method_decorator(ratelimit(key="user", rate="60/h", method="POST", block=True), name="dispatch")
class ShippingAddressActionView(AddressBookMixin, View):
    http_method_names = ["post"]
    action = ""

    def post(self, request, pk):
        address = get_object_or_404(request.user.shipping_addresses, pk=pk)
        if self.action == "default":
            addresses.set_default(request.user, address)
        elif self.action == "delete":
            addresses.delete_shipping(request.user, address)
        return self.shipping_section(request)


# ---- email-code two-factor ---------------------------------------------------
@method_decorator(ratelimit(key="user_or_ip", rate="30/h", method="POST", block=True), name="post")
class TwoFactorChallengeView(FormView):
    template_name = "accounts/two_factor.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")
        if not twofactor.needs_challenge(request):
            return redirect(self.default_destination())
        return super().dispatch(request, *args, **kwargs)

    def default_destination(self) -> str:
        return reverse("admin:index") if self.request.user.is_staff else reverse("catalog:home")

    def get_form(self, form_class=None):
        return TwoFactorCodeForm(**self.get_form_kwargs(), allow_remember=not self.request.user.is_staff)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["masked_email"] = mask_email(self.request.user.email)
        return ctx

    def get(self, request, *args, **kwargs):
        if twofactor.current_code(request.user) is None:  # first visit (or expired): email a code once
            try:
                twofactor.start(request.user)
            except OTPError as exc:
                messages.error(request, str(exc))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        code_obj = twofactor.current_code(self.request.user)
        if code_obj is None:
            form.add_error("code", "This code has expired. Request a new one.")
            return self.form_invalid(form)
        try:
            twofactor.verify(code_obj, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", str(exc))
            return self.form_invalid(form)
        destination = twofactor.pop_next(self.request, self.default_destination())  # read before the flag clears it
        if self.request.user.is_staff and not destination.startswith(reverse("admin:index")):
            destination = self.default_destination()  # staff land in the admin, not the storefront home
        twofactor.mark_verified(self.request)
        response = redirect(destination)
        if form.cleaned_data.get("remember"):
            twofactor.set_trusted_cookie(response, self.request.user)
        return response


@method_decorator(ratelimit(key="user_or_ip", rate="10/h", method="POST", block=True), name="post")
class TwoFactorResendView(LoginRequiredMixin, View):
    def post(self, request):
        if not twofactor.needs_challenge(request):
            return redirect("accounts:account")
        code_obj = twofactor.current_code(request.user)
        try:
            if code_obj is None:
                twofactor.start(request.user)
            else:
                twofactor.resend(code_obj)
            messages.success(request, f"A new code was sent to {mask_email(request.user.email)}.")
        except OTPError as exc:
            messages.error(request, str(exc))
        return redirect("accounts:two_factor")


class SecurityView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/security.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["disable_form"] = kwargs.get("disable_form") or DisableTwoFactorForm(user=self.request.user)
        return ctx


@method_decorator(ratelimit(key="user", rate="20/h", method="POST", block=True), name="post")
class EnableTwoFactorView(LoginRequiredMixin, View):
    def post(self, request):
        user = request.user
        if not user.email_verified:
            messages.error(request, "Verify your email address first — the codes are sent there.")
        else:
            user.two_factor_enabled = True
            user.save(update_fields=["two_factor_enabled"])
            twofactor.mark_verified(request)  # they're already signed in; the next login will ask
            messages.success(request, "Sign-in codes are on. We'll email you a code each time you sign in.")
        return redirect("accounts:security")


@method_decorator(ratelimit(key="user", rate="20/h", method="POST", block=True), name="post")
class DisableTwoFactorView(LoginRequiredMixin, View):
    def post(self, request):
        user = request.user
        if user.is_staff or user.is_superuser:
            messages.error(request, "Sign-in codes are required for staff accounts.")
            return redirect("accounts:security")
        form = DisableTwoFactorForm(request.POST, user=user)
        if not form.is_valid():
            return render(request, "accounts/security.html", {"disable_form": form})
        user.two_factor_enabled = False
        user.save(update_fields=["two_factor_enabled"])
        user.login_codes.all().delete()
        messages.success(request, "Sign-in codes are off.")
        return redirect("accounts:security")
