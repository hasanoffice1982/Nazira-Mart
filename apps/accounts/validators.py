import re

from django import forms

PHONE_RE = re.compile(r"^(?:\+?88)?01[3-9]\d{8}$")


def clean_bd_phone(value: str) -> str:
    phone = re.sub(r"[\s-]", "", value or "")
    if not PHONE_RE.match(phone):
        raise forms.ValidationError("Enter a valid Bangladeshi mobile number.")
    return phone
