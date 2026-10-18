from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog import image_import, importer
from apps.catalog.models import Product


class Command(BaseCommand):
    help = (
        "Attach photos from a ZIP to products listed in a CSV, directly in the terminal (no web request, no background thread).\n"
        "Products that already have photos are skipped, so you can run it again after an interruption."
    )

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=True, help="the same CSV you imported, e.g. /home/salespal/products.csv")
        parser.add_argument("--zip", required=True, action="append", help="photos ZIP (repeat for several parts)")
        parser.add_argument("--replace", action="store_true", help="also redo products that already have photos")

    def handle(self, *args, **o):
        for path in [o["csv"], *o["zip"]]:
            if not Path(path).is_file():
                raise CommandError(f"File not found: {path}")
        bank = image_import.ImageBank(o["zip"])
        if bank.error:
            raise CommandError(bank.error)
        plan = importer.parse(Path(o["csv"]).read_bytes(), bank=bank)
        if plan.errors:
            for line, msg in plan.errors[:20]:
                self.stdout.write(self.style.ERROR(f"line {line}: {msg}"))
            raise CommandError("The CSV has errors; nothing was changed.")
        entries = plan.image_entries
        done = skipped = failed = 0
        for n, entry in enumerate(entries, 1):
            product = Product.objects.filter(slug=entry["slug"]).first()
            if product is None:
                self.stdout.write(self.style.WARNING(f"{entry['slug']}: product not found (import the CSV first)"))
                failed += 1
                continue
            if product.images.exists() and not o["replace"]:
                skipped += 1
                continue
            try:
                image_import._process_product(entry, bank)
                done += 1
                self.stdout.write(f"[{n}/{len(entries)}] {entry['slug']}")
            except Exception as exc:  # noqa: BLE001  one product must not stop the rest
                failed += 1
                self.stdout.write(self.style.ERROR(f"[{n}/{len(entries)}] {entry['slug']}: {exc}"))
        self.stdout.write(self.style.SUCCESS(f"photos added to {done} products, skipped {skipped} (already had photos), failed {failed}"))
