from django.contrib import admin, messages
from django.http import JsonResponse
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt
from tinymce.widgets import TinyMCE

from . import services
from .models import Category, Post, Tag


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "position")
    list_editable = ("position",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "state", "published_at", "updated_at")
    list_filter = ("is_published", "category", "tags")
    search_fields = ("title", "body", "meta_description")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("category", "tags", "related_products")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Article", {"fields": ("title", "slug", "category", "tags", "body")}),
        ("Cover image", {"fields": ("cover_image", "cover_alt")}),
        ("Search engines and sharing", {"fields": ("seo_title", "meta_description", "excerpt")}),
        ("Shop these products (optional)", {"fields": ("related_products",)}),
        ("Publishing", {"fields": ("is_published", "published_at", "created_at", "updated_at")}),
    )

    @admin.display(description="Status")
    def state(self, obj):
        if not obj.is_published:
            return "Draft"
        return "Live" if obj.is_live else "Scheduled"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "body":
            kwargs["widget"] = TinyMCE(mce_attrs={
                "height": 560,
                "plugins": "link lists image table autoresize code",
                "toolbar": "blocks | bold italic underline | bullist numlist | link image table blockquote hr | removeformat code | undo redo",
                "valid_elements": "p,br,strong/b,em/i,u,s,h2,h3,h4,ul,ol,li,blockquote,hr,a[href|title],img[src|alt|width|height],figure,figcaption,table,thead,tbody,tr,th[colspan|rowspan],td[colspan|rowspan]",
                "images_upload_url": reverse("admin:blog_post_upload_image"),
                "images_upload_credentials": True,
                "automatic_uploads": True,
                "paste_data_images": True,
                "image_description": True,
                "image_caption": True,
                "image_dimensions": False,
            })
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def get_urls(self):
        return [path("upload-image/", self.admin_site.admin_view(csrf_exempt(self.upload_image)), name="blog_post_upload_image")] + super().get_urls()

    def upload_image(self, request):
        """Editor image button: stores the picture on this site and gives the editor its address. Staff only; a stranger's web page cannot send the XHR header, so it cannot trigger this."""
        if request.method != "POST" or request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"error": "Bad request."}, status=400)
        if not (self.has_add_permission(request) or self.has_change_permission(request)):
            return JsonResponse({"error": "Not allowed."}, status=403)
        try:
            url = services.store_inline_image(request.FILES.get("file"))
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({"location": url})

    def save_model(self, request, obj, form, change):
        foreign = services.foreign_images(form.cleaned_data.get("body", ""))
        super().save_model(request, obj, form, change)
        if foreign:
            messages.warning(request, f"{len(foreign)} image(s) from other websites were removed (they would not load on narizmart.com). Upload them with the editor's image button instead: {', '.join(foreign[:3])}")
