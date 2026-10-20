from django.contrib import admin
from django.utils.html import format_html

from apps.orders.admin import OrderAdmin

from .models import ChatOrder


class ViaFilter(admin.SimpleListFilter):
    title = "ordered via"
    parameter_name = "via"

    def lookups(self, request, model_admin):
        return [("website", "Website"), *ChatOrder.Channel.choices]

    def queryset(self, request, queryset):
        if self.value() == "website":
            return queryset.filter(chat_order__isnull=True)
        if self.value() in ChatOrder.Channel.values:
            return queryset.filter(chat_order__channel=self.value())
        return queryset


@admin.register(ChatOrder)
class ChatOrderAdmin(admin.ModelAdmin):
    list_display = ("order", "channel", "channel_user_id", "created_at")
    list_filter = ("channel",)
    search_fields = ("order__number", "channel_user_id", "request_id")
    readonly_fields = [f.name for f in ChatOrder._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.display(description="Via")
def order_via(self, obj):
    chat = getattr(obj, "chat_order", None)
    return format_html('<span class="badge badge-success">{}</span>', chat.get_channel_display()) if chat else "Website"


OrderAdmin.list_display = (*OrderAdmin.list_display, "via")
OrderAdmin.list_filter = (*OrderAdmin.list_filter, ViaFilter)
OrderAdmin.via = order_via
_previous_get_queryset = OrderAdmin.get_queryset
OrderAdmin.get_queryset = lambda self, request: _previous_get_queryset(self, request).select_related("user", "chat_order")   # naming "user" too: Django skips list_select_related once a queryset has its own select_related
