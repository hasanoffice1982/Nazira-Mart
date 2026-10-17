import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import DeliveryArea


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Order placed"
        CONFIRMED = "confirmed", "Confirmed"
        PROCESSING = "processing", "Packing"
        SHIPPED = "shipped", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        COD = "cod", "Cash on delivery"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"

    Area = DeliveryArea

    number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.COD)
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=80)
    delivery_area = models.CharField(max_length=10, choices=Area.choices, default=Area.DHAKA)
    notes = models.CharField(max_length=255, blank=True)

    # Billing / present-address snapshot (for invoices)
    billing_name = models.CharField(max_length=120, blank=True)
    billing_phone = models.CharField(max_length=20, blank=True)
    billing_address = models.CharField(max_length=255, blank=True)
    billing_city = models.CharField(max_length=80, blank=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    promotion = models.ForeignKey("orders.Promotion", on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    promotion_label = models.CharField(max_length=120, blank=True)  # snapshot: the name stays even if the promotion is edited or deleted
    coupon_code = models.CharField(max_length=30, blank=True)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)

    tracking_number = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.number

    def get_absolute_url(self) -> str:
        return reverse("orders:detail", args=[self.number])

    @staticmethod
    def new_number() -> str:
        return "BM-" + uuid.uuid4().hex[:8].upper()


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, blank=True)
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.SET_NULL, null=True, blank=True)
    # Immutable snapshot at purchase time
    variant_label = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=255)  # snapshot of Product.name
    sku = models.CharField(max_length=40)
    art_type = models.CharField(max_length=30, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    def __str__(self) -> str:
        return f"{self.name} × {self.quantity}"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    status = models.CharField(max_length=12, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        verbose_name_plural = "order status history"


class CheckoutOTP(models.Model):
    """A checkout awaiting email confirmation. No Order, stock change or account exists until it is verified."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    payload = models.JSONField(help_text="Delivery details + cart lines [[product_id, qty], ...]")
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    resends = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]


class Promotion(models.Model):
    """A discount campaign. No code = applied automatically at checkout; with a code = the customer types it in.

    Only one discount applies per order: whichever saves the customer the most.
    """

    class Kind(models.TextChoices):
        PERCENT = "percent", "Percent off"
        FIXED = "fixed", "Fixed amount off"

    name = models.CharField(max_length=120, help_text="Shown to the customer, e.g. “10% off orders over ৳5,000”")
    code = models.CharField(max_length=30, blank=True, help_text="Leave empty for an automatic discount. Otherwise customers type this at checkout (not case sensitive).")
    kind = models.CharField("Discount type", max_length=8, choices=Kind.choices, default=Kind.PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))], help_text="Percent (1–100) or an amount in your currency")
    min_subtotal = models.DecimalField("Minimum order", max_digits=12, decimal_places=2, default=0, help_text="Products total (before delivery) needed to qualify. 0 = no minimum.")
    max_discount = models.DecimalField("Maximum discount", max_digits=12, decimal_places=2, null=True, blank=True, help_text="Optional cap, mostly for percent discounts")
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Empty = starts now")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Empty = never ends")
    usage_limit = models.PositiveIntegerField("Total uses allowed", null=True, blank=True, help_text="Empty = unlimited. Cancelled orders don't count.")
    per_customer_limit = models.PositiveIntegerField("Uses per customer", null=True, blank=True, help_text="Empty = unlimited, 1 = once per customer")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-is_active", "-created_at")
        constraints = [
            models.UniqueConstraint(Upper("code"), condition=~models.Q(code=""), name="promotion_code_unique_ci"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})" if self.code else self.name

    @property
    def is_automatic(self) -> bool:
        return not self.code

    def clean(self):
        if self.kind == self.Kind.PERCENT and self.value is not None and self.value > 100:
            raise ValidationError({"value": "A percent discount can't be more than 100."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The end must be after the start."})

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def in_window(self, now=None) -> bool:
        now = now or timezone.now()
        return (self.starts_at is None or self.starts_at <= now) and (self.ends_at is None or now < self.ends_at)

    def amount_for(self, subtotal: Decimal) -> Decimal:
        if self.kind == self.Kind.PERCENT:
            amount = (subtotal * self.value / Decimal(100)).quantize(Decimal("0.01"))
        else:
            amount = self.value
        if self.max_discount is not None:
            amount = min(amount, self.max_discount)
        return max(Decimal(0), min(amount, subtotal))
