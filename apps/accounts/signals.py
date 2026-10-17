from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.cart.services import merge_session_cart

from . import twofactor


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs) -> None:
    if request is None:
        return
    merge_session_cart(request, user)
    # Every fresh password login starts unverified; a valid trusted-device cookie (customers only) skips the code.
    request.session.pop(twofactor.VERIFIED_KEY, None)
    if user.requires_two_factor and twofactor.is_trusted_device(request, user):
        twofactor.mark_verified(request)
