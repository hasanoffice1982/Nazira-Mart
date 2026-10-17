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
