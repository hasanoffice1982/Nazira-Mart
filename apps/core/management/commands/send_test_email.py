from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email with the configured backend (use after changing SMTP settings)."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Recipient address (use your own address until your domain is verified)")

    def handle(self, *args, to, **options):
        self.stdout.write(f"Backend: {settings.EMAIL_BACKEND}\nFrom:    {settings.DEFAULT_FROM_EMAIL}\nHost:    {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        try:
            sent = send_mail(f"{settings.SITE_NAME} test email", "If you can read this, outgoing email works.", None, [to])
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Sending failed: {exc}")
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} message to {to}"))
