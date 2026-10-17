from django import forms

from apps.accounts.models import PresentAddress
from apps.accounts.validators import clean_bd_phone
from apps.core.forms import HoneypotMixin

from .models import Order

NEW = "new"
PRESENT = "present"
REQUIRED_WHEN_NEW = ("full_name", "phone", "address", "city", "delivery_area")


class CheckoutForm(HoneypotMixin, forms.Form):
    email = forms.EmailField(help_text="We'll email a 6-digit code to confirm your order.")
    address_choice = forms.ChoiceField(label="Deliver to", widget=forms.RadioSelect, required=False)
    full_name = forms.CharField(max_length=120, required=False)
    phone = forms.CharField(max_length=20, required=False, help_text="e.g. 01712345678")
    address = forms.CharField(max_length=255, required=False, label="Street address")
    city = forms.CharField(max_length=80, required=False, label="City / District")
    delivery_area = forms.ChoiceField(choices=Order.Area.choices, widget=forms.RadioSelect, initial=Order.Area.DHAKA, required=False)
    notes = forms.CharField(max_length=255, required=False, label="Delivery notes (optional)")
    save_address = forms.BooleanField(required=False, label="Save this address to my address book")
    field_order = ["email", "address_choice", "full_name", "phone", "address", "city", "delivery_area", "notes", "save_address"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved: dict = {}
        self.present: PresentAddress | None = None
        authed = user is not None and user.is_authenticated
        if authed:
            if user.email_verified:
                del self.fields["email"]  # verified customers check out directly
            else:
                self.fields["email"].initial = user.email
                self.fields["email"].disabled = True  # must confirm the address on their account
            self._build_picker(user)
        else:
            del self.fields["address_choice"], self.fields["save_address"]

    def _build_picker(self, user) -> None:
        addresses = list(user.shipping_addresses.all())
        self.saved = {f"ship:{a.pk}": a for a in addresses}
        self.present = PresentAddress.objects.filter(user=user).first()
        choices = [(f"ship:{a.pk}", f"{a.get_label_display()} — {a.full_name}, {a.one_line}" + ("  (default)" if a.is_default else "")) for a in addresses]
        if self.present:
            choices.append((PRESENT, f"My present address — {self.present.full_name}, {self.present.one_line}"))
        if not choices:
            del self.fields["address_choice"]
            return
        choices.append((NEW, "Deliver to a new address"))
        field = self.fields["address_choice"]
        field.choices = choices
        default = next((a for a in addresses if a.is_default), None)
        field.initial = f"ship:{default.pk}" if default else (PRESENT if self.present else NEW)

    @property
    def uses_new_address(self) -> bool:
        return self.cleaned_data.get("address_choice") in (None, "", NEW)

    def clean_phone(self) -> str:
        phone = self.cleaned_data.get("phone", "")
        return clean_bd_phone(phone) if phone else ""

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        cleaned = super().clean()
        choice = cleaned.get("address_choice") or NEW
        if choice in self.saved:
            cleaned.update(self.saved[choice].as_data())
        elif choice == PRESENT and self.present:
            cleaned.update({**self.present.as_data(), "notes": cleaned.get("notes", "")})
        else:
            for name in REQUIRED_WHEN_NEW:
                if not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, "This field is required.")
        if choice != NEW:
            cleaned["save_address"] = False  # nothing new to save
        return cleaned


class OTPForm(forms.Form):
    code = forms.CharField(
        label="6-digit code", min_length=6, max_length=6,
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "pattern": "[0-9]{6}", "autofocus": True}),
    )

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Enter the 6 digits from the email.")
        return code
