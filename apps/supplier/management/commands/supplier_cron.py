"""One command for cron (e.g. every 30 minutes): refresh products, retry failed orders, warn when the wallet is low."""
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from apps.supplier import client, importer, services

logger = logging.getLogger("apps.supplier")


class Command(BaseCommand):
    help = "Run all supplier automation: product sync, retry failed orders, low-balance alert. Safe to run often."

    def handle(self, *args, **options):
        if not client.configured():
            self.stdout.write("Supplier keys not set: nothing to do.")
            return
        steps = (("retry", self.retry), ("balance", self.balance), ("products", self.products))
        for name, fn in steps:
            try:
                self.stdout.write(f"{name}: {fn()}")
            except client.SupplierError as exc:
                logger.warning("supplier_cron %s failed: %s", name, exc)
                self.stdout.write(self.style.WARNING(f"{name}: {exc}"))
            except Exception:  # noqa: BLE001  one step failing must not stop the others
                logger.exception("supplier_cron %s crashed", name)
                self.stdout.write(self.style.ERROR(f"{name}: crashed (see log)"))

    def retry(self) -> str:
        return f"{services.retry_failed()} parcel(s) sent on retry"

    def balance(self) -> str:
        data = client.balance()
        current = float(data.get("current_balance") or 0)
        if current < settings.SUPPLIER_LOW_BALANCE and cache.add("supplier_low_balance_mail", 1, 6 * 3600):
            send_mail(
                "NarizMart: supplier wallet is low",
                f"Your supplier wallet balance is {current:.2f} BDT (alert level {settings.SUPPLIER_LOW_BALANCE}). "
                f"Orders fail below {data.get('minimum_required', 200)} BDT. Please top up.",
                None, [settings.SUPPORT_EMAIL], fail_silently=True,
            )
            return f"balance {current:.2f} is LOW, alert emailed"
        return f"balance {current:.2f} ok"

    def products(self) -> str:
        r = importer.sync(
            include=settings.SUPPLIER_SYNC_INCLUDE, exclude=settings.SUPPLIER_SYNC_EXCLUDE, limit=settings.SUPPLIER_SYNC_LIMIT,
            activate=settings.SUPPLIER_SYNC_ACTIVATE, only_known_category=True,
            create_new=bool(settings.SUPPLIER_SYNC_INCLUDE),   # no include list = only refresh price and stock
        )
        return f"seen {r.seen}, new {r.created}, refreshed {r.updated}, photos {r.images}"
