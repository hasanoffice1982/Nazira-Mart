import logging
import re
import traceback

from adminsortable2.admin import SortableAdminBase, SortableInlineAdminMixin
from django import forms
from django.contrib import admin
from django.http import HttpResponse

from .widgets import RichTextAdminMixin
from .models import Benefit, ContactMessage, HeroSlide, NewsletterSubscriber, Page, PromoBanner, SiteSettings


def _field(label, help_text, rows=2):
    return forms.CharField(label=label, required=False, help_text=help_text, widget=forms.Textarea(attrs={"rows": rows, "spellcheck": "false"}))


class SiteSettingsForm(forms.ModelForm):
    """Tracking codes can be pasted whole (the snippet from Meta or Google); only the ID is kept and the site builds the safe snippet itself."""

    meta_pixel_id = _field("Meta (Facebook) Pixel", "Paste your Pixel ID (e.g. 123456789012345) or the whole code from Events Manager. Empty = off.")
    google_tag_id = _field("Google tag", "Paste your Google Analytics (G-…), Google Ads (AW-…) or Tag Manager (GTM-…) ID, or the whole code. Empty = off.")

    tiktok_pixel_id = _field("TikTok Pixel", "Paste your TikTok Pixel ID (from TikTok Ads Manager > Events) or the whole code. Empty = off.")
    tiktok_domain_verification = _field("TikTok domain verification", "Paste the whole <meta> tag TikTok gives you, or just its content code. Empty = off.", rows=1)
    google_site_verification = _field("Google Search Console verification", "Paste the whole <meta> tag Google gives you, or just its content code. Empty = off.", rows=1)
    meta_domain_verification = _field("Meta domain verification", "Paste the whole <meta> tag Meta gives you, or just its content code. Empty = off.", rows=1)

    class Meta:
        model = SiteSettings
        fields = "__all__"

    PROFILE_LINKS = (("tiktok_url", "https://www.tiktok.com/@"), ("instagram_url", "https://www.instagram.com/"))

    def __init__(self, data=None, *args, **kwargs):
        if data is not None:   # turn "@narizmart" into a link BEFORE the URL field validates it
            data = data.copy()
            for name, base in self.PROFILE_LINKS:
                if name in data:
                    data[name] = self._profile_url(data[name], base)
        super().__init__(data, *args, **kwargs)

    @staticmethod
    def _verification_code(raw: str, label: str) -> str:
        raw = raw.strip()
        if not raw:
            return ""
        found = re.search(r"content\s*=\s*[\"']([A-Za-z0-9_-]{10,100})[\"']", raw) or re.fullmatch(r"([A-Za-z0-9_-]{10,100})", raw)
        if not found:
            raise forms.ValidationError(f"Couldn't find the {label} code. Paste the whole <meta> tag or just the code inside content=\"…\".")
        return found.group(1)

    def clean_google_site_verification(self):
        return self._verification_code(self.cleaned_data["google_site_verification"], "Google")

    def clean_meta_domain_verification(self):
        return self._verification_code(self.cleaned_data["meta_domain_verification"], "Meta")

    @staticmethod
    def _profile_url(raw: str, base: str) -> str:
        """Accept a full link, a link without https, or just a handle like @narizmart."""
        raw = (raw or "").strip()
        if raw and not raw.startswith(("http://", "https://")) and "." not in raw.split("/")[0] and " " not in raw:
            return base + raw.lstrip("@/")
        return raw

    def clean_tiktok_domain_verification(self):
        return self._verification_code(self.cleaned_data["tiktok_domain_verification"], "TikTok")

    def clean_tiktok_pixel_id(self):
        raw = self.cleaned_data["tiktok_pixel_id"].strip()
        if not raw:
            return ""
        found = re.search(r"ttq\.load\(\s*['\"]([A-Za-z0-9]{10,30})['\"]", raw) or re.fullmatch(r"([A-Za-z0-9]{10,30})", raw)
        if not found:
            raise forms.ValidationError("Couldn't find a TikTok Pixel ID in that. It looks like C1234567890ABCDEFGHI.")
        return found.group(1)

    def clean_meta_pixel_id(self):
        raw = self.cleaned_data["meta_pixel_id"].strip()
        if not raw:
            return ""
        found = re.search(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"]?(\d{8,20})", raw) or re.search(r"\b(\d{8,20})\b", raw)
        if not found:
            raise forms.ValidationError("Couldn't find a Pixel ID in that. It is a number of 15-16 digits.")
        return found.group(1)

    def clean_google_tag_id(self):
        raw = self.cleaned_data["google_tag_id"].strip()
        if not raw:
            return ""
        found = re.search(r"\b((?:GTM|GT|G|AW)-[A-Za-z0-9]{4,20})\b", raw, re.IGNORECASE)
        if not found:
            raise forms.ValidationError("Couldn't find an ID like G-ABC123XYZ, AW-123456789 or GTM-ABCD123 in that.")
        return found.group(1).upper()


class HeroSlideInline(SortableInlineAdminMixin, RichTextAdminMixin, admin.StackedInline):
    """Slides of the home banner, edited right here: click "Add another Hero slide" for more, drag by the handle to reorder."""

    model = HeroSlide
    extra = 0
    rich_fields = ("text",)
    verbose_name = "Hero slide"
    verbose_name_plural = "Hero slides"
    fieldsets = (
        (None, {"fields": ("is_active", "eyebrow", "title", "highlight", "text")}),
        ("Buttons", {"fields": ("button_label", "button_url", "button2_label", "button2_url")}),
        ("Picture", {"fields": ("image", "product", "bubble_label", "art")}),
        ("Background and look", {"fields": ("bg_color", "bg_color2", "text_style")}),
        ("Extras", {"fields": ("float_title", "float_text", "proof_text"), "classes": ("collapse",)}),
    )


class PromoBannerInline(SortableInlineAdminMixin, RichTextAdminMixin, admin.StackedInline):
    """Banners of the promo slider, edited right here: click "Add another Promo banner" for more, drag by the handle to reorder."""

    model = PromoBanner
    extra = 0
    rich_fields = ("text",)
    verbose_name = "Promo banner"
    verbose_name_plural = "Promo banners"
    fieldsets = (
        (None, {"fields": ("is_active", "tag", "title", "highlight", "text")}),
        ("Button", {"fields": ("button_label", "button_url")}),
        ("Picture", {"fields": ("image", "art")}),
        ("Background and look", {"fields": ("theme", "bg_color", "bg_color2", "text_style")}),
    )


@admin.register(SiteSettings)
class SiteSettingsAdmin(SortableAdminBase, RichTextAdminMixin, admin.ModelAdmin):
    form = SiteSettingsForm
    inlines = [HeroSlideInline, PromoBannerInline]
    rich_fields = ("story_text", "hero_text", "promo_text", "footer_about")
    fieldsets = (
        ("Currency & delivery pricing", {"fields": ("currency_symbol", "currency_code", "delivery_charge_dhaka", "delivery_charge_sub", "delivery_charge_outside", "free_delivery_threshold"), "description": "These control the delivery charge added at checkout and the currency symbol everywhere. Existing orders keep the price they were placed at."}),
        ("Layout & size", {"fields": ("layout_mode", "layout_side_padding", "layout_box_width", "layout_hero_height"), "description": "Switch the page width and the home banner height to see which you prefer. Changes can take up to 5 minutes to reach every visitor."}),
        ("Announcement bar", {"fields": ("announcement", "announcement_extra")}),
        ("Story section", {"fields": ("story_label", "story_title", "story_highlight", "story_text", "story_points", "story_art", "story_image")}),
        ("Newsletter", {"fields": ("newsletter_title", "newsletter_text")}),
        ("Advertising & tracking codes", {"fields": ("meta_pixel_id", "tiktok_pixel_id", "google_tag_id", "track_shopping_events", "google_site_verification", "meta_domain_verification", "tiktok_domain_verification"), "description": "Paste your codes here; no code changes needed. The site loads them on every page and sends shopping events (view product, add to cart, checkout, purchase). Test with Meta Pixel Helper or Google Tag Assistant."}),
        ("Photo limits", {"fields": ("max_photos_per_product", "bulk_upload_max_files"), "description": "How many photos can be listed per product in a CSV import, and sent in one bulk upload. Each photo is up to 5 MB and is compressed automatically."}),
        ("Slider speed", {"fields": ("hero_slider_seconds", "promo_slider_seconds"), "description": "How fast the home banner and the promo banners slide by themselves (0 = they do not slide by themselves). Add or change the slides in the Hero slides and Promo banners tabs."}),
        ("Popular products section", {"fields": ("show_popular", "popular_mode", "popular_count", "popular_fallback_count", "popular_days", "popular_label", "popular_title", "popular_subtitle"), "description": "Shown on the home page between the categories and the product grid."}),
        ("Home page headings & buttons", {"fields": (
            "nav_max_categories", "nav_note",
            "categories_label", "categories_title", "categories_subtitle", "view_all_label",
            "products_label", "products_title", "products_subtitle")}),
        ("Footer & contact", {"fields": ("company_name", "footer_about", "footer_contact_text", "contact_email", "contact_phone", "contact_address", "footer_tagline")}),
        ("Social links", {"fields": ("facebook_url", "instagram_url", "tiktok_url", "youtube_url", "linkedin_url"), "description": "Only links you fill in appear in the footer, as icons."}),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Benefit)
class BenefitAdmin(admin.ModelAdmin):
    list_display = ("title", "icon", "position", "is_active")
    list_editable = ("position", "is_active")


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at")
    search_fields = ("email",)


@admin.register(Page)
class PageAdmin(RichTextAdminMixin, admin.ModelAdmin):
    rich_fields = ("body",)
    list_display = ("title", "slug", "group", "position", "is_published", "updated_at")
    list_editable = ("position", "is_published")
    list_filter = ("group", "is_published")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "order_number", "created_at", "handled")
    list_filter = ("handled",)
    list_editable = ("handled",)
    search_fields = ("name", "email", "order_number", "message")
    readonly_fields = ("name", "email", "phone", "order_number", "message", "created_at")

    def has_add_permission(self, request):
        return False


# --- django-axes admin pages --------------------------------------------------------------------------------------
# axes ships its three log pages with a date drill-down (date_hierarchy). On MariaDB without the time-zone tables (shared hosting)
# that drill-down fails and the page returns a 500, the same reason the Orders list has none. The date filter on the right stays.
from axes.admin import AccessAttemptAdmin, AccessFailureLogAdmin, AccessLogAdmin  # noqa: E402
from axes.models import AccessAttempt, AccessFailureLog, AccessLog  # noqa: E402



class ShowErrorToSuperusersMixin:
    """If the page fails, log it and show the error text to superusers instead of a bare 500 (the server log is not always reachable on shared hosting)."""

    def changelist_view(self, request, extra_context=None):
        try:
            response = super().changelist_view(request, extra_context)
            if hasattr(response, "render"):
                response.render()   # template errors surface here, not above
            return response
        except Exception:  # noqa: BLE001
            logging.getLogger("apps.core").exception("admin list failed: %s", request.path)
            if request.user.is_superuser:
                return HttpResponse(traceback.format_exc(), content_type="text/plain; charset=utf-8", status=500)
            raise


for _model, _admin in ((AccessAttempt, AccessAttemptAdmin), (AccessLog, AccessLogAdmin), (AccessFailureLog, AccessFailureLogAdmin)):
    if admin.site.is_registered(_model):
        admin.site.unregister(_model)
    admin.site.register(_model, type(_admin.__name__, (ShowErrorToSuperusersMixin, _admin), {"date_hierarchy": None}))
