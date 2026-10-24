import csv

from django.contrib import admin
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from apps.catalog.models import Product, ProductVariant

from .models import Cart, CartItem, GuestCart


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("user", "updated_at")
    inlines = [CartItemInline]


class ContactFilter(admin.SimpleListFilter):
    title = "contact details"
    parameter_name = "contact"

    def lookups(self, request, model_admin):
        return [("yes", "We have an email or phone"), ("no", "No contact details")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(email="", phone="")
        if self.value() == "no":
            return queryset.filter(email="", phone="")
        return queryset


class OutcomeFilter(admin.SimpleListFilter):
    title = "outcome"
    parameter_name = "outcome"

    def lookups(self, request, model_admin):
        return [("left", "Left items in the bag, no order"), ("ordered", "Placed an order"), ("empty", "Bag emptied")]

    def queryset(self, request, queryset):
        if self.value() == "left":
            return queryset.filter(order__isnull=True, item_count__gt=0)
        if self.value() == "ordered":
            return queryset.filter(order__isnull=False)
        if self.value() == "empty":
            return queryset.filter(order__isnull=True, item_count=0)
        return queryset


def _wa_number(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    return "88" + digits if digits.startswith("01") else digits


@admin.register(GuestCart)
class GuestCartAdmin(admin.ModelAdmin):
    """Signed-out visitors' bags, newest first. Filter to "Left items in the bag" + "We have an email or phone" to get a follow-up list."""

    list_display = ("session", "contact", "bag", "item_count", "outcome", "updated_at")
    list_filter = (OutcomeFilter, ContactFilter, ("updated_at", admin.DateFieldListFilter))
    search_fields = ("email", "phone", "name", "session_key")
    readonly_fields = ("session_key", "bag", "item_count", "name", "email", "phone", "order", "created_at", "updated_at")
    fields = readonly_fields
    actions = ["export_csv"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Visitor")
    def session(self, obj):
        return obj.session_key[:8]

    @admin.display(description="Contact")
    def contact(self, obj):
        parts = []
        if obj.name:
            parts.append(format_html("<b>{}</b>", obj.name))
        if obj.phone:
            parts.append(format_html('<a href="tel:{}">{}</a> · <a href="https://wa.me/{}" target="_blank" rel="noopener">WhatsApp</a>', obj.phone, obj.phone, _wa_number(obj.phone)))
        if obj.email:
            parts.append(format_html('<a href="mailto:{}">{}</a>', obj.email, obj.email))
        return format_html_join(" · ", "{}", ((p,) for p in parts)) if parts else "—"

    def _lines(self, obj) -> list[str]:
        products = {p.pk: p.name for p in Product.objects.filter(pk__in=[i[0] for i in obj.items])}
        variants = {v.pk: v.label for v in ProductVariant.objects.select_related("color", "size").filter(pk__in=[i[1] for i in obj.items if i[1]])}
        return [f"{products.get(pid, 'Removed product')}{f' ({variants[vid]})' if vid in variants else ''} × {qty}" for pid, vid, qty in obj.items]

    @admin.display(description="Bag")
    def bag(self, obj):
        lines = self._lines(obj)
        return format_html_join(format_html("<br>"), "{}", ((l,) for l in lines)) if lines else "—"

    @admin.display(description="Outcome")
    def outcome(self, obj):
        if obj.order_id:
            return format_html('Ordered · <a href="{}">{}</a>', reverse("admin:orders_order_change", args=[obj.order_id]), obj.order.number)
        return "Left items" if obj.item_count else "Bag emptied"

    @admin.action(description="Download selected as CSV")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="guest-bags.csv"'
        writer = csv.writer(response)
        writer.writerow(["Updated", "Name", "Phone", "Email", "Items", "Outcome"])
        for obj in queryset.select_related("order"):
            writer.writerow([obj.updated_at.strftime("%Y-%m-%d %H:%M"), obj.name, obj.phone, obj.email, "; ".join(self._lines(obj)), obj.order.number if obj.order_id else ("left items" if obj.item_count else "emptied")])
        return response
