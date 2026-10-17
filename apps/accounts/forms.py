from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, UserCreationForm

from apps.core.forms import HoneypotMixin

from .models import PresentAddress, ShippingAddress
from .validators import clean_bd_phone

User = get_user_model()


class EmailLoginForm(HoneypotMixin, AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    password = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember_me = forms.BooleanField(label="Keep me signed in", required=False, initial=True)

    def confirm_login_allowed(self, user) -> None:
        super().confirm_login_allowed(user)
        if user.is_staff or user.is_superuser:
            raise forms.ValidationError(
                "Staff accounts can't sign in to the storefront. Use the admin site, or a separate customer account.",
                code="staff_not_allowed",
            )


class RegisterForm(HoneypotMixin, UserCreationForm):
    class Meta:
        model = User
        fields = ("first_name", "email", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["phone"].required = False
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self) -> str:
        phone = self.cleaned_data.get("phone", "")
        return clean_bd_phone(phone) if phone else ""


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone")

    def clean_phone(self) -> str:
        phone = self.cleaned_data.get("phone", "")
        return clean_bd_phone(phone) if phone else ""


class HoneypotPasswordResetForm(HoneypotMixin, PasswordResetForm):
    def get_users(self, email):
        # Unlike Django's default, include accounts with no password yet (auto-created at checkout)
        return (u for u in User.objects.filter(email__iexact=email, is_active=True))


class _PhoneCleanMixin:
    def clean_phone(self) -> str:
        return clean_bd_phone(self.cleaned_data["phone"])


class PresentAddressForm(_PhoneCleanMixin, forms.ModelForm):
    class Meta:
        model = PresentAddress
        fields = ("full_name", "phone", "address", "city", "delivery_area")
        widgets = {"delivery_area": forms.RadioSelect}


class ShippingAddressForm(_PhoneCleanMixin, forms.ModelForm):
    make_default = forms.BooleanField(label="Make this my default address", required=False)

    class Meta:
        model = ShippingAddress
        fields = ("label", "full_name", "phone", "address", "city", "delivery_area", "notes")
        widgets = {"delivery_area": forms.RadioSelect, "label": forms.Select}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["make_default"].initial = self.instance.is_default


class TwoFactorCodeForm(forms.Form):
    code = forms.CharField(
        label="6-digit code", min_length=6, max_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "pattern": "[0-9]{6}", "autofocus": True}),
    )
    remember = forms.BooleanField(label="Don't ask again on this device for 30 days", required=False)

    def __init__(self, *args, allow_remember=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_remember:
            del self.fields["remember"]

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Enter the 6 digits from the email.")
        return code


class DisableTwoFactorForm(forms.Form):
    password = forms.CharField(label="Confirm with your password", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("That password isn't right.")
        return password
