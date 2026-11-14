from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Product, ProductVariant

from apps.orders.services import create_order


class Command(BaseCommand):
    help = "Place a throw-away test order so the [NEW ORDER] staff email can be checked, then remove it and restore the stock."

    def add_arguments(self, parser):
        parser.add_argument("--keep", action="store_true", help="keep the test order instead of deleting it")

    def handle(self, *args, keep=False, **opts):
        user = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError("No superuser found to place the test order with.")
        with_variants = ProductVariant.objects.filter(is_active=True).values_list("product_id", flat=True)
        product = Product.objects.filter(is_active=True, stock__gt=0).exclude(pk__in=with_variants).first()
        if product is None:
            raise CommandError("No simple in-stock product (without options) to order.")
        data = {"full_name": "Test Order", "phone": "01700000000", "address": "Test address, please ignore", "city": "Dhaka", "delivery_area": "dhaka", "notes": "TEST ORDER (will be deleted)"}
        order = create_order(user=user, lines=[(product.pk, None, 1)], data=data, auto_confirm=False)
        self.stdout.write(f"Placed {order.number}; the alert was sent to the ORDER_NOTIFY_EMAILS addresses.")
        if keep:
            return
        with transaction.atomic():
            Product.objects.filter(pk=product.pk).update(stock=product.stock)
            order.delete()
        self.stdout.write("Test order removed and stock restored.")
