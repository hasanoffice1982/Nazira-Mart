from django.contrib import admin, messages
from django.utils.html import format_html

from apps.orders.models import Order
from apps.orders.admin import OrderAdmin

from . import client, services
from .models import SupplierOrder, SupplierProduct


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("supplier_product_id", "product", "base_price", "commission", "final_price", "supplier_stock", "last_synced")
    search_fields = ("supplier_product_id", "product__name")
    readonly_fields = ("last_synced",)
    autocomplete_fields = ()


@admin.register(SupplierOrder)
class SupplierOrderAdmin(admin.ModelAdmin):
    list_display = ("partner_order_id", "order", "state", "supplier_status", "courier_name", "tracking_code", "error", "sent_at")
    list_filter = ("state", "supplier_status")
    search_fields = ("partner_order_id", "supplier_order_id", "order__number", "tracking_code")
    readonly_fields = [f.name for f in SupplierOrder._meta.fields]

    def has_add_permission(self, request):
        return False


class SupplierOrderInline(admin.TabularInline):
    model = SupplierOrder
    extra = 0
    can_delete = False
    fields = ("partner_order_id", "state", "supplier_status", "courier_name", "tracking_link_link", "error")
    readonly_fields = fields

    @admin.display(description="Tracking")
    def tracking_link_link(self, obj):
        return format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', obj.tracking_link, obj.tracking_code or "track") if obj.tracking_link else obj.tracking_code

    def has_add_permission(self, request, obj=None):
        return False


@admin.action(description="Send to supplier (confirmed orders)")
def send_to_supplier(modeladmin, request, queryset):
    if not client.configured():
        modeladmin.message_user(request, "Supplier keys are not set on the server.", messages.ERROR)
        return
    for order in queryset:
        if order.status not in (Order.Status.CONFIRMED, Order.Status.PROCESSING):
            modeladmin.message_user(request, f"{order.number}: confirm the order first.", messages.WARNING)
            continue
        parcels = services.send_order(order)
        if not parcels:
            modeladmin.message_user(request, f"{order.number}: no supplier products in this order.", messages.INFO)
        for p in parcels:
            level = messages.SUCCESS if p.state == SupplierOrder.State.SENT else messages.ERROR
            modeladmin.message_user(request, f"{p.partner_order_id}: {p.get_state_display()} {p.error}".strip(), level)


# plug the action and the parcels list into the existing Order admin
OrderAdmin.actions = [*OrderAdmin.actions, send_to_supplier]
OrderAdmin.inlines = [*OrderAdmin.inlines, SupplierOrderInline]
