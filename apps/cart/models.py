from django.conf import settings
from django.db import models
from django.db.models import Q


class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")
    updated_at = models.DateTimeField(auto_now=True)


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE)
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.CASCADE, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cart", "product", "variant"], name="uniq_cartitem_cart_product_variant"),
            models.UniqueConstraint(fields=["cart", "product"], condition=Q(variant__isnull=True), name="uniq_cartitem_cart_product_novariant"),
        ]


class GuestCart(models.Model):
    """A snapshot of a signed-out visitor's bag (the live cart stays in their session) so staff can follow up on abandoned bags.
    Contact details are filled in when the visitor types them at checkout, even if they never place the order."""

    session_key = models.CharField(max_length=40, unique=True)
    items = models.JSONField(default=list, help_text="[[product_id, variant_id, quantity], ...]")
    item_count = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="guest_carts", help_text="Set when this visitor placed the order")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"Guest bag {self.session_key[:8]}"

    @property
    def has_contact(self) -> bool:
        return bool(self.email or self.phone)
