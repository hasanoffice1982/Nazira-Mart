import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse


def new_token() -> str:
    return secrets.token_urlsafe(24)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


class Dealer(models.Model):
    """A seller we buy from who is NOT on the dropshipping API. Orders for their products go out to an automation (n8n / Make),
    which tells the dealer (WhatsApp, email, a shared sheet) and posts the courier and tracking back to the shop."""

    name = models.CharField(max_length=60, unique=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    webhook_url = models.URLField("Automation webhook URL", max_length=400, blank=True, help_text="The n8n (or Make) production webhook that receives this dealer's orders. Leave empty to handle this dealer's orders by hand.")
    secret = models.CharField(max_length=64, default=new_secret, help_text="Signs every order sent out (header X-NarizMart-Signature, HMAC-SHA256 of the body) so the automation can check it came from the shop.")
    token = models.CharField(max_length=48, unique=True, default=new_token, help_text="Secret part of the address the automation uses to send tracking back.")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def callback_url(self) -> str:
        return f"{settings.SITE_URL.rstrip('/')}{reverse('dealers:webhook', args=[self.token])}"


class DealerOrder(models.Model):
    """The part of one customer order that a dealer must ship (all their items together)."""

    class State(models.TextChoices):
        PENDING = "pending", "Not sent yet"
        SENT = "sent", "Sent to dealer"
        FAILED = "failed", "Sending failed"
        MANUAL = "manual", "Handle by hand"

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="dealer_orders")
    dealer = models.ForeignKey(Dealer, on_delete=models.PROTECT, related_name="orders")
    reference = models.CharField(max_length=40, unique=True, help_text="Order number plus dealer, e.g. NM-1234-2. The automation sends it back with the tracking.")
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    dealer_status = models.CharField(max_length=40, blank=True)
    courier_name = models.CharField(max_length=60, blank=True)
    tracking_code = models.CharField(max_length=80, blank=True)
    tracking_link = models.URLField(max_length=300, blank=True)
    error = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)
        constraints = [models.UniqueConstraint(fields=["order", "dealer"], name="uniq_dealer_order")]

    def __str__(self) -> str:
        return self.reference
