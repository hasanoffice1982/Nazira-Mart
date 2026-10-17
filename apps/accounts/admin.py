from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import PresentAddress, ShippingAddress, User


class PresentAddressInline(admin.StackedInline):
    model = PresentAddress
    extra = 0
    can_delete = False


class ShippingAddressInline(admin.TabularInline):
    model = ShippingAddress
    extra = 0
    fields = ("label", "full_name", "phone", "address", "city", "delivery_area", "is_default")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    inlines = [PresentAddressInline, ShippingAddressInline]
    list_display = ("email", "first_name", "phone", "email_verified", "two_factor_enabled", "is_staff", "date_joined")
    list_filter = ("is_staff", "is_active", "email_verified")
    search_fields = ("email", "first_name", "phone")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "phone", "email_verified", "email_verified_at", "two_factor_enabled")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
