"""Address-book rules: single default, 10-address cap, safe default promotion."""
from __future__ import annotations

from django.db import transaction

from .models import PresentAddress, ShippingAddress


class AddressLimitError(Exception):
    pass


def can_add(user) -> bool:
    return user.shipping_addresses.count() < ShippingAddress.MAX_PER_USER


@transaction.atomic
def save_shipping(user, address: ShippingAddress, *, make_default: bool = False) -> ShippingAddress:
    """Create or update a shipping address while keeping exactly one default."""
    creating = address.pk is None
    if creating and not can_add(user):
        raise AddressLimitError(f"You can save up to {ShippingAddress.MAX_PER_USER} addresses. Delete one first.")
    others = user.shipping_addresses.exclude(pk=address.pk)
    address.user = user
    address.is_default = make_default or address.is_default or not others.exists()  # the first address is the default
    if address.is_default:
        others.filter(is_default=True).update(is_default=False)
    address.save()
    return address


@transaction.atomic
def set_default(user, address: ShippingAddress) -> None:
    user.shipping_addresses.exclude(pk=address.pk).filter(is_default=True).update(is_default=False)
    if not address.is_default:
        address.is_default = True
        address.save(update_fields=["is_default"])


@transaction.atomic
def delete_shipping(user, address: ShippingAddress) -> None:
    was_default = address.is_default
    address.delete()
    if was_default:  # promote the newest remaining address
        newest = user.shipping_addresses.order_by("-created_at", "-id").first()
        if newest:
            newest.is_default = True
            newest.save(update_fields=["is_default"])


def save_present(user, data: dict) -> PresentAddress:
    present, _ = PresentAddress.objects.update_or_create(user=user, defaults=data)
    return present
