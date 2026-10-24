from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.cart.models import GuestCart


class Command(BaseCommand):
    help = "Delete old signed-out bags (default: untouched for 60 days) so personal details are not kept forever. Run weekly from cron."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60)

    def handle(self, *args, **options):
        deleted, _ = GuestCart.objects.filter(updated_at__lt=timezone.now() - timedelta(days=options["days"])).delete()
        self.stdout.write(f"Deleted {deleted} old guest bags")
