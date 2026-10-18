from django import forms
from django.contrib import admin, messages
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html

from apps.orders.models import Order
from apps.orders.admin import OrderAdmin

from . import client, importer, services
from .models import SupplierOrder, SupplierProduct


class ImportForm(forms.Form):
    include = forms.CharField(required=False, label="Only titles containing", help_text="Words separated by | e.g. baby|frock|three piece. Empty = everything.")
    exclude = forms.CharField(required=False, label="Skip titles containing", help_text="e.g. men|islamic")
    limit = forms.IntegerField(min_value=1, max_value=50, initial=10, label="Max new products", help_text="Keep it small: photos are downloaded and the page waits for them.")
    preview = forms.BooleanField(required=False, initial=True, label="Preview only", help_text="Show what would be imported, change nothing.")
    refresh_only = forms.BooleanField(required=False, label="Only refresh price and stock", help_text="Do not add new products.")
    activate = forms.BooleanField(required=False, label="Make new products visible at once", help_text="Leave off to review them first.")


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("supplier_product_id", "product", "base_price", "commission", "final_price", "supplier_stock", "last_synced")
    search_fields = ("supplier_product_id", "product__name")
    readonly_fields = ("last_synced",)
    autocomplete_fields = ()

    def get_urls(self):
        return [path("import/", self.admin_site.admin_view(self.import_view), name="supplier_supplierproduct_import"), *super().get_urls()]

    def import_view(self, request):
        context = {**self.admin_site.each_context(request), "title": "Import from supplier", "opts": self.model._meta}
        form = ImportForm(request.POST or None)
        context["form"] = form
        if not self.has_change_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if request.method == "POST" and form.is_valid():
            d = form.cleaned_data
            if not client.configured():
                context["error"] = "Supplier keys are not set on the server."
            else:
                try:
                    context["report"] = importer.sync(
                        include=d["include"], exclude=d["exclude"], limit=d["limit"], activate=d["activate"],
                        dry_run=d["preview"], create_new=not d["refresh_only"],
                    )
                    context["dry_run"] = d["preview"]
                except client.SupplierError as exc:
                    context["error"] = str(exc)
        return TemplateResponse(request, "admin/supplier/supplierproduct/import.html", context)


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
