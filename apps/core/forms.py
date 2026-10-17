from django import forms

HONEYPOT_NAME = "hp_contact"  # odd name so password managers don't autofill it


class HoneypotInput(forms.TextInput):
    is_honeypot = True

    def __init__(self):
        super().__init__(attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"})


class HoneypotField(forms.CharField):
    widget = HoneypotInput

    def __init__(self, *args, **kwargs):
        kwargs.update(required=False, label="")
        super().__init__(*args, **kwargs)

    def validate(self, value):
        super().validate(value)
        if value:  # humans never see this field
            raise forms.ValidationError("Invalid submission.", code="honeypot")


class HoneypotMixin(forms.Form):
    """Add to any public form. Bots that fill every input get a silent validation failure."""

    hp_contact = HoneypotField()


class ContactForm(HoneypotMixin):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, required=False, label="Phone (optional)")
    order_number = forms.CharField(max_length=20, required=False, label="Order number (optional)", help_text="e.g. BM-1A2B3C4D")
    message = forms.CharField(min_length=10, max_length=2000, widget=forms.Textarea(attrs={"rows": 6}))

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean_order_number(self) -> str:
        return self.cleaned_data["order_number"].strip().upper()
