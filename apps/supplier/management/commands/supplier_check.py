from django.core.management.base import BaseCommand

from apps.supplier import client


class Command(BaseCommand):
    help = "Test the supplier connection: keys, IP whitelist, wallet balance and the product list (prints no secrets)."

    def handle(self, *args, **options):
        if not client.configured():
            self.stdout.write(self.style.ERROR("SUPPLIER_API_KEY / SUPPLIER_API_SECRET are not set."))
            return
        try:
            data = client.balance()
            self.stdout.write(self.style.SUCCESS(f"Balance OK: {data.get('current_balance')} {data.get('currency', '')} (minimum {data.get('minimum_required')})"))
        except client.SupplierError as exc:
            self.stdout.write(self.style.ERROR(f"Balance failed: {exc}"))
        try:
            catalog = client.products()
            self.stdout.write(self.style.SUCCESS(f"Products OK: {len(catalog)} items"))
        except client.SupplierError as exc:
            self.stdout.write(self.style.ERROR(f"Products failed: {exc}  (if HTTP 404, set SUPPLIER_PATH_PRODUCTS)"))
