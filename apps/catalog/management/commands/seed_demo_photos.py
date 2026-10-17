from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from apps.catalog.models import Product, ProductImage

PHOTO_DIR = Path(settings.BASE_DIR) / "sample_data" / "demo_photos"


class Command(BaseCommand):
    help = "Attach the demo gallery photos (sample_data/demo_photos/<slug>-<n>.webp) to the matching products. Products that already have photos are skipped unless --force."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Replace existing gallery photos of those products")

    def handle(self, *args, **options):
        added = skipped = 0
        for product in Product.objects.all():
            files = sorted(PHOTO_DIR.glob(f"{product.slug}-*.webp"))
            if not files:
                continue
            if product.images.exists():
                if not options["force"]:
                    skipped += 1
                    continue
                product.images.all().delete()
            for position, path in enumerate(files):
                ProductImage.objects.create(
                    product=product, image=ContentFile(path.read_bytes(), name=path.name), alt_text=product.name[:160], position=position
                )
                added += 1
        self.stdout.write(self.style.SUCCESS(f"Added {added} photo(s); {skipped} product(s) already had photos."))
