from django.core.management.base import BaseCommand

from apps.catalog.models import Product
from apps.supplier.links import link_product


class Command(BaseCommand):
    help = (
        "One-time helper: mark existing products as dropship (linked to the supplier) using their SKU as the supplier id. "
        "By default only products whose SKU is all digits (the numbers from the supplier panel) and that have no dealer and no link yet."
    )

    def add_arguments(self, parser):
        parser.add_argument("--exclude", default="", help="comma-separated SKUs to leave alone, e.g. 7191,7192")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **o):
        skip = {s.strip() for s in o["exclude"].split(",") if s.strip()}
        todo = [p for p in Product.objects.filter(dealer__isnull=True, supplier_link__isnull=True) if p.sku.isdigit() and p.sku not in skip]
        for p in todo:
            self.stdout.write(f"  {p.sku}  {p.name[:60]}")
            if not o["dry_run"]:
                link_product(p, p.sku, price=p.price)
        self.stdout.write(self.style.SUCCESS(f"{'would link' if o['dry_run'] else 'linked'} {len(todo)} product(s) as dropship"))
