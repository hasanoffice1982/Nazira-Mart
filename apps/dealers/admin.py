from django.contrib import admin, messages
from django.db.models import Count
from django.utils.html import format_html_join

from apps.orders.admin import OrderAdmin
from apps.orders.models import Order

from . import services
from .models import Dealer, DealerOrder

KIND_STYLE = {"dropship": "background:#dbeafe;color:#1e3a8a", "dealer": "background:#fef3c7;color:#78350f", "own": "background:#e5e7eb;color:#1f2937"}   # dark text on a light pill: the theme's badge classes render white text


class FulfilmentFilter(admin.SimpleListFilter):
    """Who ships it: the dropship supplier (API), a named dealer, or your own stock. Works on Products and on Orders."""

    title = "who ships it"
    parameter_name = "ships"
    prefix = ""   # "" for products, "items__product__" for orders

    def lookups(self, request, model_admin):
        return [("dropship", "Dropship (supplier API)"), ("own", "Own stock"), *[(f"dealer-{d.pk}", f"Dealer: {d.name}") for d in Dealer.objects.all()]]

    def queryset(self, request, queryset):
        value, p = self.value(), self.prefix
        if value == "dropship":
            return queryset.filter(**{f"{p}supplier_link__isnull": False}).distinct()
        if value == "own":
            return queryset.filter(**{f"{p}supplier_link__isnull": True, f"{p}dealer__isnull": True}).distinct()
        if value and value.startswith("dealer-") and value[7:].isdigit():
            return queryset.filter(**{f"{p}dealer_id": int(value[7:])}).distinct()
        return queryset


class OrderFulfilmentFilter(FulfilmentFilter):
    prefix = "items__product__"


@admin.register(Dealer)
class DealerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "has_webhook", "is_active", "product_count", "open_orders")
    list_editable = ("is_active",)
    search_fields = ("name", "phone", "email")
    readonly_fields = ("tracking_address", "token")
    fieldsets = (
        (None, {"fields": ("name", "phone", "email", "is_active", "notes")}),
        ("Automation (n8n / Make)", {
            "fields": ("webhook_url", "secret", "tracking_address", "token"),
            "description": "Orders for this dealer's products are POSTed to the webhook URL when you confirm the order. "
                           "The automation sends courier and tracking back to the tracking address below (POST, JSON).",
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_products=Count("products", distinct=True))

    @admin.display(boolean=True, description="Automation")
    def has_webhook(self, obj):
        return bool(obj.webhook_url)

    @admin.display(description="Products", ordering="_products")
    def product_count(self, obj):
        return obj._products

    @admin.display(description="Open orders")
    def open_orders(self, obj):
        return obj.orders.exclude(order__status__in=(Order.Status.DELIVERED, Order.Status.CANCELLED, Order.Status.RETURNED)).count()

    @admin.display(description="Tracking address (give this to the automation)")
    def tracking_address(self, obj):
        return obj.callback_url if obj.pk else "Save the dealer first."


@admin.register(DealerOrder)
class DealerOrderAdmin(admin.ModelAdmin):
    list_display = ("reference", "order", "dealer", "state", "dealer_status", "courier_name", "tracking_code", "error", "sent_at")
    list_filter = ("dealer", "state", "dealer_status")
    search_fields = ("reference", "order__number", "tracking_code")
    readonly_fields = [f.name for f in DealerOrder._meta.fields]

    def has_add_permission(self, request):
        return False


class DealerOrderInline(admin.TabularInline):
    model = DealerOrder
    extra = 0
    can_delete = False
    fields = ("reference", "dealer", "state", "dealer_status", "courier_name", "tracking_code", "error")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.action(description="Send to dealers (confirmed orders)")
def send_to_dealers(modeladmin, request, queryset):
    for order in queryset:
        if order.status not in (Order.Status.CONFIRMED, Order.Status.PROCESSING):
            modeladmin.message_user(request, f"{order.number}: confirm the order first.", messages.WARNING)
            continue
        parts = services.send_order(order)
        if not parts:
            modeladmin.message_user(request, f"{order.number}: no dealer products in this order.", messages.INFO)
        for p in parts:
            level = messages.SUCCESS if p.state == DealerOrder.State.SENT else messages.ERROR
            modeladmin.message_user(request, f"{p.reference}: {p.get_state_display()} {p.error}".strip(), level)


@admin.display(description="Ships from")
def order_fulfilment(self, obj):
    seen = {}
    for item in obj.items.all():
        p = item.product
        if p is not None:
            kind, label = p.fulfilment
            seen[label] = kind
    if not seen:
        return "—"
    return format_html_join(" ", '<span style="{};padding:2px 8px;border-radius:999px;font-weight:600">{}</span>', ((KIND_STYLE[k], label) for label, k in seen.items()))


# plug dealer features into the existing Order admin (same pattern as the supplier app)
OrderAdmin.actions = [*OrderAdmin.actions, send_to_dealers]
OrderAdmin.inlines = [*OrderAdmin.inlines, DealerOrderInline]
OrderAdmin.list_display = (*OrderAdmin.list_display, "fulfilment")
OrderAdmin.list_filter = (*OrderAdmin.list_filter, OrderFulfilmentFilter)
OrderAdmin.fulfilment = order_fulfilment
_orig_get_queryset = OrderAdmin.get_queryset
OrderAdmin.get_queryset = lambda self, request: _orig_get_queryset(self, request).prefetch_related("items__product__dealer", "items__product__supplier_link")


from apps.catalog.admin import ProductAdmin  # noqa: E402  (after the Order admin wiring above)

ProductAdmin.list_filter = (*ProductAdmin.list_filter, FulfilmentFilter)
