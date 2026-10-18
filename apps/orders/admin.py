from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Order, OrderItem, OrderStatusHistory, Promotion
from .services import OrderError, change_status


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product", "name", "sku", "unit_price", "quantity")

    def has_add_permission(self, request, obj=None):
        return False


class HistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("status", "note", "changed_by", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "user", "status_badge", "payment_status", "total", "created_at")
    list_select_related = ("user",)
    # no date_hierarchy: it needs MySQL time-zone tables, which shared hosting does not have (admin list returned a 500)
    list_filter = ("status", "payment_status", "delivery_area", ("created_at", admin.DateFieldListFilter))
    search_fields = ("number", "user__email", "phone", "full_name")
    readonly_fields = ("number", "user", "subtotal", "discount", "promotion", "promotion_label", "coupon_code", "delivery_charge", "total", "payment_method", "created_at", "updated_at", "billing_name", "billing_phone", "billing_address", "billing_city")
    inlines = [OrderItemInline, HistoryInline]
    actions = ["mark_confirmed", "mark_processing", "mark_shipped", "mark_delivered", "mark_returned"]

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return format_html('<span class="status status-{}">{}</span>', obj.status, obj.get_status_display())

    def save_model(self, request, obj, form, change):
        if change and "status" in form.changed_data:
            new_status = obj.status
            obj.status = form.initial["status"]  # let the service validate the transition
            try:
                change_status(obj, new_status, by=request.user)
            except OrderError as exc:
                messages.error(request, str(exc))
            # persist other edited fields without clobbering status
            obj.refresh_from_db(fields=["status", "payment_status"])
            form.changed_data = [f for f in form.changed_data if f != "status"]
        super().save_model(request, obj, form, change)

    def _bulk(self, request, queryset, status):
        for order in queryset:
            try:
                change_status(order, status, by=request.user)
            except OrderError as exc:
                self.message_user(request, f"{order.number}: {exc}", messages.WARNING)

    @admin.action(description="Mark as Confirmed")
    def mark_confirmed(self, request, queryset):
        self._bulk(request, queryset, Order.Status.CONFIRMED)

    @admin.action(description="Mark as Packing")
    def mark_processing(self, request, queryset):
        self._bulk(request, queryset, Order.Status.PROCESSING)

    @admin.action(description="Mark as Out for delivery")
    def mark_shipped(self, request, queryset):
        self._bulk(request, queryset, Order.Status.SHIPPED)

    @admin.action(description="Mark as Delivered")
    def mark_delivered(self, request, queryset):
        self._bulk(request, queryset, Order.Status.DELIVERED)

    @admin.action(description="Mark as Returned / refused (stock goes back)")
    def mark_returned(self, request, queryset):
        self._bulk(request, queryset, Order.Status.RETURNED)


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("name", "code_or_auto", "offer", "min_subtotal", "window", "times_used", "is_active")
    list_editable = ("is_active",)
    list_filter = ("is_active", "kind")
    search_fields = ("name", "code")
    readonly_fields = ("times_used",)
    fieldsets = (
        (None, {"fields": ("name", "code", "is_active"), "description": "No code = applied automatically to every order that qualifies. Only one discount applies per order: whichever saves the customer most."}),
        ("Discount", {"fields": ("kind", "value", "max_discount", "min_subtotal")}),
        ("Schedule and limits", {"fields": ("starts_at", "ends_at", "usage_limit", "per_customer_limit", "times_used")}),
    )

    @admin.display(description="Code")
    def code_or_auto(self, obj):
        return obj.code or "Automatic"

    @admin.display(description="Offer")
    def offer(self, obj):
        return f"{obj.value.normalize():f}%" if obj.kind == Promotion.Kind.PERCENT else f"{obj.value.normalize():f} off"

    @admin.display(description="Runs")
    def window(self, obj):
        if not obj.starts_at and not obj.ends_at:
            return "Always"
        start = f"{obj.starts_at:%d %b %Y}" if obj.starts_at else "…"
        end = f"{obj.ends_at:%d %b %Y}" if obj.ends_at else "…"
        return f"{start} → {end}"

    @admin.display(description="Times used")
    def times_used(self, obj):
        return obj.orders.exclude(status__in=Order.INACTIVE_STATUSES).count() if obj.pk else 0
