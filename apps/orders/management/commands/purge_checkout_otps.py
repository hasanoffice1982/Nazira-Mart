from django.core.management.base import BaseCommand

from apps.accounts.twofactor import purge_expired as purge_login_codes
from apps.orders.otp import purge_expired


class Command(BaseCommand):
    help = "Delete expired checkout confirmations and sign-in codes (run daily from cron)."

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {purge_expired()} checkout confirmations and {purge_login_codes()} sign-in codes")
