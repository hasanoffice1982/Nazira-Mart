from django.core.management.base import BaseCommand

from apps.catalog.models import Product


class Command(BaseCommand):
    help = "Hide products by SKU (is_active=False). Nothing is deleted, so past orders keep their history. Shows what it would do unless --apply is given."

    def add_arguments(self, parser):
        parser.add_argument("skus", help="comma-separated SKUs, e.g. 877,3590,8346")
        parser.add_argument("--apply", action="store_true", help="actually hide them (default: only list)")

    def handle(self, *args, **o):
        skus = {s.strip() for s in o["skus"].split(",") if s.strip()}
        found = list(Product.objects.filter(sku__in=skus))
        for p in found:
            self.stdout.write(f"  {p.sku}  {'(already hidden) ' if not p.is_active else ''}{p.name[:60]}")
        missing = sorted(skus - {p.sku for p in found})
        if missing:
            self.stdout.write(self.style.WARNING(f"not found: {', '.join(missing)}"))
        todo = [p.pk for p in found if p.is_active]
        if o["apply"]:
            Product.objects.filter(pk__in=todo).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"{'hid' if o['apply'] else 'would hide'} {len(todo)} product(s)"))
