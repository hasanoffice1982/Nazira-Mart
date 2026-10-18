from decimal import Decimal

from django.core.cache import cache
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator, URLValidator
from django.db import models

from .richtext import sanitize_field  # noqa: E402

CACHE_KEY = "site_settings"
_https_only = URLValidator(schemes=["http", "https"])  # blocks javascript:, data:, ftp: and friends in link fields
_phone = RegexValidator(r"^[0-9+()\-\s]{5,30}$", "Use digits, spaces, + ( ) and - only, e.g. +880 1712-345678")


class SiteSettings(models.Model):
    """Singleton: all editable storefront copy for the home page and announcement bar."""

    # --- money: currency and delivery pricing (used by checkout, emails and the storefront) ---
    currency_symbol = models.CharField(max_length=6, default="৳", help_text="Shown before every price, e.g. ৳ or Tk")
    delivery_charge_dhaka = models.DecimalField("delivery charge inside Dhaka", max_digits=8, decimal_places=2, default=Decimal("60"), validators=[MinValueValidator(0)])
    delivery_charge_sub = models.DecimalField("delivery charge Dhaka sub-area", max_digits=8, decimal_places=2, default=Decimal("100"), validators=[MinValueValidator(0)], help_text="Savar, Gazipur, Narayanganj, Keraniganj and similar areas around Dhaka (the courier charges more there).")
    delivery_charge_outside = models.DecimalField("delivery charge outside Dhaka", max_digits=8, decimal_places=2, default=Decimal("120"), validators=[MinValueValidator(0)])
    free_delivery_threshold = models.DecimalField(
        "free delivery from", max_digits=10, decimal_places=2, default=Decimal("5000"), validators=[MinValueValidator(0)],
        help_text="Orders of this amount or more get free delivery. Enter 0 to turn free delivery off (then also edit the announcement and Shipping page text).",
    )

    # --- page layout: try both and keep the one you like ---
    layout_mode = models.CharField("Page width", max_length=5, default="wide", choices=[
        ("wide", "Wide: full screen width with space on each side"),
        ("boxed", "Boxed: centered, at most 1336 px wide"),
    ], help_text="Wide fills big screens. Boxed keeps the content in a centered column. Save, then refresh the shop to compare.")
    layout_side_padding = models.PositiveSmallIntegerField("Side space in wide mode (px)", default=96, validators=[MinValueValidator(16), MaxValueValidator(220)], help_text="Space on the left and right of the page on big screens (90–100 looks balanced). Smaller screens shrink it by themselves.")
    layout_hero_height = models.CharField("Home banner height", max_length=7, default="normal", choices=[
        ("compact", "Compact (shorter)"), ("normal", "Normal"), ("tall", "Tall (bigger picture)"),
    ])

    # --- footer, contact details and social links ---
    company_name = models.CharField(max_length=60, default="NarizMart", help_text="Used in the footer copyright line")
    footer_about = models.TextField(default="Pretty finds for ladies and little ones. Fashion, beauty, toys and kids' favourites, picked with love in Bangladesh.")
    footer_contact_text = models.CharField(max_length=200, blank=True, default="Questions, feedback, or just want to say hello? We're always happy to hear from you.")
    contact_email = models.EmailField(blank=True, help_text="Shown in the footer and receives contact-form messages. Leave empty to use the server's SUPPORT_EMAIL setting.")
    contact_phone = models.CharField(max_length=30, blank=True, validators=[_phone], help_text="Optional, e.g. +880 1712-345678")
    contact_address = models.CharField(max_length=160, blank=True, default="Bangladesh 🇧🇩", help_text="Optional address or location line")
    footer_tagline = models.CharField(max_length=80, blank=True, default="Made with love in Bangladesh ♡", help_text="Right side of the bottom bar")
    facebook_url = models.URLField(blank=True, validators=[_https_only], help_text="Full link, e.g. https://facebook.com/yourpage. Empty = icon hidden.")
    instagram_url = models.URLField(blank=True, validators=[_https_only])
    tiktok_url = models.URLField(blank=True, validators=[_https_only], help_text="Your TikTok profile link. Empty = icon hidden.")
    youtube_url = models.URLField(blank=True, validators=[_https_only])
    linkedin_url = models.URLField(blank=True, validators=[_https_only])

    # --- home page headings and button labels ---
    hero_slider_count = models.PositiveSmallIntegerField("Slides in the home slider", default=3, validators=[MinValueValidator(1), MaxValueValidator(7)], help_text="1 to 7, including the intro slide. 1 = no slider.")
    hero_show_intro = models.BooleanField("First slide is the intro text above", default=True)
    hero_slider_source = models.CharField("Fill the other slides with", max_length=7, default="popular", choices=[
        ("popular", "Popular products (pinned, then best sellers, then newest)"),
        ("newest", "Newest products"),
    ], help_text="Filled from your products automatically; nothing to update by hand.")
    hero_slider_seconds = models.PositiveSmallIntegerField("Seconds per slide", default=6, validators=[MaxValueValidator(30)], help_text="0 = don't slide automatically")
    currency_code = models.CharField("Currency code (for ads and analytics)", max_length=3, default="BDT", validators=[RegexValidator(r"^[A-Za-z]{3}$", "Use a 3-letter code such as BDT or USD.")], help_text="Reported to Meta and Google with order values")
    promo_slider_seconds = models.PositiveSmallIntegerField("Seconds per promo banner", default=6, validators=[MaxValueValidator(30)], help_text="0 = the promo banners do not slide by themselves")
    max_photos_per_product = models.PositiveSmallIntegerField("Most photos per product in a CSV import", default=20, validators=[MinValueValidator(1), MaxValueValidator(100)], help_text="Shared photos plus per-color photos listed for one product. Photos added by hand on a product page have no limit.")
    bulk_upload_max_files = models.PositiveSmallIntegerField("Most files in one bulk photo upload", default=20, validators=[MinValueValidator(1), MaxValueValidator(100)], help_text="On a product page: Upload many photos. One upload is also capped at 100 MB in total.")
    meta_pixel_id = models.CharField("Meta (Facebook) Pixel ID", max_length=20, blank=True, validators=[RegexValidator(r"^\d{8,20}$", "A Pixel ID is 8-20 digits.")])
    google_tag_id = models.CharField("Google tag / Analytics / Ads / Tag Manager ID", max_length=24, blank=True, validators=[RegexValidator(r"^(G|GT|GTM|AW)-[A-Z0-9]{4,20}$", "Use an ID like G-ABC123XYZ, GTM-ABCD123 or AW-123456789.")])
    track_shopping_events = models.BooleanField("Send shopping events", default=True, help_text="Product views, add to cart, checkout started and purchases (with order value), for retargeting and conversion reports")
    google_site_verification = models.CharField("Google Search Console verification", max_length=100, blank=True, validators=[RegexValidator(r"^[A-Za-z0-9_-]{10,100}$", "Paste only the verification code.")])
    tiktok_pixel_id = models.CharField("TikTok Pixel ID", max_length=30, blank=True, validators=[RegexValidator(r"^[A-Za-z0-9]{10,30}$", "A TikTok Pixel ID is 10-30 letters and digits.")])
    tiktok_domain_verification = models.CharField("TikTok domain verification", max_length=100, blank=True, validators=[RegexValidator(r"^[A-Za-z0-9_-]{10,100}$", "Paste only the verification code.")])
    meta_domain_verification = models.CharField("Meta domain verification", max_length=100, blank=True, validators=[RegexValidator(r"^[A-Za-z0-9_-]{10,100}$", "Paste only the verification code.")])
    show_popular = models.BooleanField("Show Popular products section", default=True)
    popular_mode = models.CharField("How to choose popular products", max_length=6, default="mix", choices=[
        ("mix", "Pinned products first, then automatic best sellers (recommended)"),
        ("auto", "Automatic best sellers only"),
        ("manual", "Pinned products only"),
    ], help_text="Pin products on their edit page (Home page: Popular products). Best sellers come from real orders; cancelled orders don't count.")
    popular_count = models.PositiveSmallIntegerField("Products shown once there are sales", default=6, validators=[MinValueValidator(4), MaxValueValidator(12)])
    popular_fallback_count = models.PositiveSmallIntegerField("Products shown before any sale", default=4, validators=[MinValueValidator(1), MaxValueValidator(12)], help_text="Newest products fill the gaps until orders come in")
    popular_days = models.PositiveSmallIntegerField("Count sales from the last N days", default=90, validators=[MaxValueValidator(3650)], help_text="0 = all time")
    popular_label = models.CharField(max_length=40, default="BEST SELLERS")
    popular_title = models.CharField(max_length=60, default="Popular products")
    popular_subtitle = models.CharField(max_length=120, blank=True, default="What everyone is adding to their cart.")
    nav_max_categories = models.PositiveSmallIntegerField("Max categories in header menu", default=4, validators=[MaxValueValidator(12)], help_text="Not used any more: the Shop All dropdown lists every category that has \"Show in header menu\" ticked, in the order set under Catalog → Categories.")
    nav_note = models.CharField(max_length=60, blank=True, default="Delivering across Bangladesh", help_text="Small note at the right of the menu bar")
    hero_button_primary = models.CharField(max_length=30, default="Explore collection")
    hero_button_secondary = models.CharField(max_length=30, default="Browse categories")
    hero_proof_text = models.CharField(max_length=60, blank=True, default="Made for everyday Bangladesh", help_text="Next to the stars under the hero buttons")
    categories_label = models.CharField(max_length=40, default="DISCOVER MORE")
    categories_title = models.CharField(max_length=60, default="Shop by category")
    categories_subtitle = models.CharField(max_length=120, blank=True, default="Everything pretty for her, everything fun for them.")
    view_all_label = models.CharField(max_length=30, default="View all products")
    products_label = models.CharField(max_length=40, default="JUST FOR YOU")
    products_title = models.CharField(max_length=60, default="Trending right now")
    products_subtitle = models.CharField(max_length=120, blank=True, default="The prettiest picks everyone is adding to their bag.")

    announcement = models.CharField(
        max_length=200, default="Cash on delivery across Bangladesh ✦ Free delivery over [[currency]][[free_delivery]]",
        help_text="You can use [[currency]], [[delivery_dhaka]], [[delivery_sub]], [[delivery_outside]] and [[free_delivery]]; they follow the values above.",
    )
    announcement_extra = models.CharField(max_length=120, blank=True, default="· new arrivals every week")

    hero_eyebrow = models.CharField(max_length=80, default="NEW SEASON · LADIES & KIDS")
    hero_title = models.CharField(max_length=80, default="Pretty things.")
    hero_highlight = models.CharField(max_length=80, default="Happy smiles.")
    hero_text = models.TextField(default="Bright, cuddly and beautifully made. Fashion and beauty for her, toys and treats for the little ones.")
    hero_art = models.CharField(max_length=30, default="handbag", help_text="Illustration key (used when no hero image)")
    hero_image = models.ImageField(upload_to="site/", blank=True)

    promo_tag = models.CharField(max_length=60, default="THE NARIZMART PARTY EDIT ✦")
    promo_title = models.CharField(max_length=120, default="Dress up. Play more.")
    promo_highlight = models.CharField(max_length=120, default="Sparkle every day.")
    promo_text = models.TextField(default="Party dresses, pretty bags and cuddly friends, gift-wrapped and ready to delight.")
    promo_button = models.CharField(max_length=40, default="Discover the collection")
    promo_art = models.CharField(max_length=30, default="dress")
    promo_image = models.ImageField(upload_to="site/", blank=True)

    story_label = models.CharField(max_length=40, default="WHY NARIZMART")
    story_title = models.CharField(max_length=80, default="Made for her.")
    story_highlight = models.CharField(max_length=80, default="Loved by little ones.")
    story_text = models.TextField(default="NarizMart is a happy corner for ladies and children. Every product is picked for softness, safety and a smile, then packed like a gift.")
    story_points = models.TextField(default="Gentle, safe materials\nGift-ready packing\nCash on delivery", help_text="One point per line")
    story_art = models.CharField(max_length=30, default="teddy")
    story_image = models.ImageField(upload_to="site/", blank=True)

    newsletter_title = models.CharField(max_length=80, default="Good things in your inbox.")
    newsletter_text = models.CharField(max_length=160, default="New drops, useful finds and little surprises — no noise.")

    class Meta:
        verbose_name = verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return "Site settings"

    @property
    def story_point_list(self) -> list[str]:
        return [p.strip() for p in self.story_points.splitlines() if p.strip()]

    # --- pricing helpers ---------------------------------------------------------------------------------------
    def charge_for(self, area: str) -> Decimal:
        return {"dhaka": self.delivery_charge_dhaka, "sub": self.delivery_charge_sub}.get(area, self.delivery_charge_outside)

    def delivery_for(self, subtotal: Decimal, area: str = "dhaka") -> Decimal:
        if subtotal <= 0 or (self.free_delivery_threshold > 0 and subtotal >= self.free_delivery_threshold):
            return Decimal(0)
        return self.charge_for(area)

    @staticmethod
    def _money(value: Decimal) -> str:
        return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"

    def render_tokens(self, text: str) -> str:
        """Fill [[currency]] [[delivery_dhaka]] [[delivery_sub]] [[delivery_outside]] [[free_delivery]] so copy always matches the real prices."""
        tokens = {
            "[[currency]]": self.currency_symbol,
            "[[delivery_dhaka]]": self._money(self.delivery_charge_dhaka),
            "[[delivery_sub]]": self._money(self.delivery_charge_sub),
            "[[delivery_outside]]": self._money(self.delivery_charge_outside),
            "[[free_delivery]]": self._money(self.free_delivery_threshold) if self.free_delivery_threshold > 0 else "",
        }
        for key, value in tokens.items():
            text = text.replace(key, value)
        return text

    @property
    def effective_contact_email(self) -> str:
        """The address shown in the footer and used for the contact form: the admin value, else the server setting."""
        return self.contact_email or settings.SUPPORT_EMAIL

    @property
    def social_links(self) -> list[tuple[str, str, str]]:
        """(label, short text, url) for each link that has been filled in."""
        candidates = [("Facebook", "f", self.facebook_url), ("Instagram", "ig", self.instagram_url), ("TikTok", "tt", self.tiktok_url), ("YouTube", "▶", self.youtube_url), ("LinkedIn", "in", self.linkedin_url)]
        return [(label, short, url) for label, short, url in candidates if url]

    @property
    def announcement_rendered(self) -> str:
        return self.render_tokens(self.announcement)

    @classmethod
    def load_fresh(cls) -> "SiteSettings":
        """Always read from the database (never the cache): prices must change the moment you save them, in every worker."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        self.google_tag_id = self.google_tag_id.strip().upper()
        self.currency_code = (self.currency_code or "BDT").strip().upper()
        for name in ("story_text", "hero_text", "promo_text", "footer_about"):
            setattr(self, name, sanitize_field(getattr(self, name)))
        super().save(*args, **kwargs)
        cache.delete(CACHE_KEY)
        cache.delete("popular_ids")

    @classmethod
    def load(cls) -> "SiteSettings":
        obj = cache.get(CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(CACHE_KEY, obj, 300)
        return obj


class Benefit(models.Model):
    ICONS = [("truck", "Truck"), ("shield-check", "Shield"), ("package-check", "Package"), ("headset", "Headset"), ("sparkles", "Sparkles"), ("map-pin", "Pin")]
    icon = models.CharField(max_length=20, choices=ICONS, default="truck")
    title = models.CharField(max_length=60)
    text = models.CharField(max_length=100)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "id")

    def __str__(self) -> str:
        return self.title


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.email


class Page(models.Model):
    """Editable content page (help, shipping, returns, privacy, terms). Body is sanitised HTML from the admin editor, see core.richtext."""

    class Group(models.TextChoices):
        CARE = "care", "Footer: Customer care"
        LEGAL = "legal", "Footer: Legal (bottom bar)"
        NONE = "none", "Not in footer"

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=120)
    body = models.TextField(help_text="Use the editor toolbar. Price tokens: [[currency]] [[delivery_dhaka]] [[delivery_sub]] [[delivery_outside]] [[free_delivery]].")
    meta_description = models.CharField(max_length=160, blank=True)
    group = models.CharField(max_length=5, choices=Group.choices, default=Group.NONE)
    position = models.PositiveSmallIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("position", "title")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        self.body = sanitize_field(self.body)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        from django.urls import reverse

        return reverse("core:page", args=[self.slug])


class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    order_number = models.CharField(max_length=20, blank=True)
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    handled = models.BooleanField(default=False)

    class Meta:
        ordering = ("handled", "-created_at")

    def __str__(self) -> str:
        return f"{self.name} — {self.created_at:%d %b %Y}"


HEX_COLOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a color like #1d3b36 (a # and 6 letters or digits), or leave empty.")


class BackgroundMixin(models.Model):
    """Background options shared by hero slides and promo banners."""

    class TextStyle(models.TextChoices):
        AUTO = "auto", "Automatic (keeps the original design)"
        LIGHT = "light", "White text (for dark backgrounds)"
        DARK = "dark", "Dark text (for light backgrounds)"

    bg_color = models.CharField("Custom background color", max_length=7, blank=True, validators=[HEX_COLOR], help_text="Optional, e.g. #112a2b. Overrides the background above. Empty = use the default look.")
    bg_color2 = models.CharField("Custom background, second color", max_length=7, blank=True, validators=[HEX_COLOR], help_text="Optional. Fades from the first color to this one. Empty = a single flat color.")
    text_style = models.CharField("Text color", max_length=5, choices=TextStyle.choices, default=TextStyle.AUTO)

    class Meta:
        abstract = True

    @property
    def background_css(self) -> str:
        """A safe CSS value (colors are validated hex) or empty when no custom background was set."""
        if not self.bg_color:
            return ""
        return f"linear-gradient(103deg,{self.bg_color},{self.bg_color2 or self.bg_color})"


def validate_slide_url(value: str) -> None:
    """A slide button may point to a section of the page (#featured), a page of this shop (/shop/) or a full http(s) link."""
    if value and not (value.startswith(("#", "/")) and not value.startswith("//")) and not value.lower().startswith(("http://", "https://")):
        raise ValidationError("Use /page-on-this-site/, #section-of-the-home-page or a full link starting with https://")


class HeroSlide(BackgroundMixin):
    """One slide of the home page banner. Slides are shown in this order; untick Active to hide one without deleting it."""

    site = models.ForeignKey(SiteSettings, null=True, blank=True, on_delete=models.CASCADE, related_name="hero_slides")  # lets Site settings edit the slides inline
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0, db_index=True)
    eyebrow = models.CharField("Small line above the title", max_length=80, blank=True)
    title = models.CharField(max_length=120)
    highlight = models.CharField("Second title line (colored)", max_length=120, blank=True)
    text = models.TextField("Text", blank=True)
    button_label = models.CharField("Main button text", max_length=30, blank=True)
    button_url = models.CharField("Main button link", max_length=300, blank=True, validators=[validate_slide_url], help_text="Empty = the linked product's page. Examples: /shop/  /product/my-product/  #featured  https://…")
    button2_label = models.CharField("Second button text", max_length=30, blank=True)
    button2_url = models.CharField("Second button link", max_length=300, blank=True, validators=[validate_slide_url])
    image = models.ImageField("Picture", upload_to="hero/", blank=True, help_text="Compressed automatically. Empty = the illustration below if you chose one, otherwise the linked product's photo.")
    art = models.CharField("Illustration (when there is no picture)", max_length=30, blank=True, help_text="Used when there is no picture: headphones, bag, lamp, watch, bottle, speaker, sneaker or sunglasses. Leave empty to use the linked product's photo.")
    product = models.ForeignKey("catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", help_text="Optional. Shows its price in a bubble, links the slide to it and supplies the picture when you add no picture or illustration.")
    bubble_label = models.CharField("Price bubble text", max_length=30, blank=True, default="Featured")
    float_title = models.CharField("Floating tag, title", max_length=40, blank=True)
    float_text = models.CharField("Floating tag, small text", max_length=40, blank=True)
    proof_text = models.CharField("Stars line", max_length=60, blank=True, help_text="Shows 5 stars with this text under the buttons, e.g. Loved by 2,000 shoppers")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "hero slide"

    def __str__(self) -> str:
        return self.title

    @property
    def link(self) -> str:
        if self.button_url:
            return self.button_url
        return self.product.get_absolute_url() if self.product_id else ""

    @property
    def display_image(self):
        if self.image:
            return self.image
        if self.art:
            return None   # an illustration was chosen on purpose
        return self.product.cover_image if self.product_id else None

    @property
    def display_art(self) -> str:
        return self.art or (self.product.art_type if self.product_id else "") or "headphones"

    def save(self, *args, **kwargs):
        self.text = sanitize_field(self.text)
        super().save(*args, **kwargs)


class PromoBanner(BackgroundMixin):
    """One banner of the home page promo slider. Add at least 3 so it scrolls nicely; drag to reorder, untick Active to hide."""

    class Theme(models.TextChoices):
        FOREST = "forest", "Forest green"
        OCEAN = "ocean", "Ocean blue"
        PLUM = "plum", "Plum"
        SAND = "sand", "Warm sand"

    site = models.ForeignKey(SiteSettings, null=True, blank=True, on_delete=models.CASCADE, related_name="promo_banners")  # lets Site settings edit the banners inline
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0, db_index=True)
    tag = models.CharField("Small line above the title", max_length=60, blank=True)
    title = models.CharField(max_length=120)
    highlight = models.CharField("Second title line", max_length=120, blank=True)
    text = models.TextField("Text", blank=True)
    button_label = models.CharField("Button text", max_length=40, blank=True)
    button_url = models.CharField("Button link", max_length=300, blank=True, validators=[validate_slide_url], help_text="Examples: /shop/  /shop/?category=fashion  /product/my-product/  #categories  https://…")
    image = models.ImageField("Picture", upload_to="promo/", blank=True, help_text="Compressed automatically and fitted inside the circle, any size. Empty = the illustration below.")
    art = models.CharField("Illustration (when there is no picture)", max_length=30, blank=True, default="lamp", help_text="headphones, bag, lamp, watch, bottle, speaker, sneaker or sunglasses")
    theme = models.CharField("Background color", max_length=6, choices=Theme.choices, default=Theme.FOREST)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "promo banner"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        self.text = sanitize_field(self.text)
        super().save(*args, **kwargs)
