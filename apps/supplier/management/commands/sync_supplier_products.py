from django.core.management.base import BaseCommand

from apps.supplier import client, importer


class Command(BaseCommand):
    help = "Import new products from the supplier (hidden until you review them) and refresh price and stock of linked ones."

    def add_arguments(self, parser):
        parser.add_argument("--include", default="", help="only titles matching this regex, e.g. 'earring|necklace|girls'")
        parser.add_argument("--exclude", default="", help="skip titles matching this regex, e.g. 'men|islamic'")
        parser.add_argument("--limit", type=int, default=0, help="create at most this many NEW products (0 = no limit)")
        parser.add_argument("--activate", action="store_true", help="make new products visible straight away (default: hidden)")
        parser.add_argument("--no-photos", action="store_true")
        parser.add_argument("--any-category", action="store_true", help="also import products whose category cannot be guessed (into 'Other')")
        parser.add_argument("--dry-run", action="store_true", help="show what would be imported, change nothing")

    def handle(self, *args, **o):
        try:
            report = importer.sync(
                include=o["include"], exclude=o["exclude"], limit=o["limit"], activate=o["activate"], photos=not o["no_photos"],
                only_known_category=not o["any_category"], dry_run=o["dry_run"],
            )
        except client.SupplierError as exc:
            self.stdout.write(self.style.ERROR(str(exc)))
            return
        for line in report.notes[:60]:
            self.stdout.write("  " + line)
        self.stdout.write(self.style.SUCCESS(
            f"{'DRY RUN: ' if o['dry_run'] else ''}seen {report.seen}, new {report.created}, refreshed {report.updated}, skipped {report.skipped}, photos {report.images}"
        ))
