from django.db import models


class SupplierProduct(models.Model):
    """Links one shop product to the supplier's catalogue item (their `product_id`, e.g. VND-105)."""

    product = models.OneToOneField("catalog.Product", on_delete=models.CASCADE, related_name="supplier_link")
    supplier_product_id = models.CharField(max_length=40, unique=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    commission = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    final_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    supplier_stock = models.PositiveIntegerField(default=0)
    last_synced = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.supplier_product_id} -> {self.product}"


class SupplierOrder(models.Model):
    """One parcel sent to the supplier for an order item. `partner_order_id` is our reference at their side."""

    class State(models.TextChoices):
        PENDING = "pending", "Not sent yet"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        MANUAL = "manual", "Needs manual order"

    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="supplier_orders")
    item = models.ForeignKey("orders.OrderItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    partner_order_id = models.CharField(max_length=40, unique=True)
    supplier_order_id = models.CharField(max_length=60, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    supplier_status = models.CharField(max_length=40, blank=True)
    courier_name = models.CharField(max_length=60, blank=True)
    tracking_code = models.CharField(max_length=80, blank=True)
    tracking_link = models.URLField(max_length=300, blank=True)
    error = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)

    def __str__(self) -> str:
        return self.partner_order_id
