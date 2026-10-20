from django.core.management.base import BaseCommand

from apps.dealers import services


class Command(BaseCommand):
    help = "Re-send dealer orders that failed (automation was down, wrong URL) for orders still waiting."

    def handle(self, *args, **o):
        self.stdout.write(self.style.SUCCESS(f"re-sent {services.retry_failed()} dealer order(s)"))
