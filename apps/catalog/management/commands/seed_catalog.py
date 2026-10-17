from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from apps.catalog.models import Category, Color, Product, ProductVariant, Size
from apps.core.models import Benefit, HeroSlide, PromoBanner, SiteSettings

CATEGORIES = [
    ("women", "Women", "Women's Fashion", "Dresses & everyday style", "dress", "cat-women"),
    ("bags", "Bags", "Bags & Purses", "Carry your colour", "handbag", "cat-bags"),
    ("shoes", "Footwear", "Heels & Shoes", "Step out in style", "heels", "cat-shoes"),
    ("beauty", "Beauty", "Beauty & Glow", "Lips, skin & shine", "lipstick", "cat-beauty"),
    ("jewellery", "Jewellery", "Jewellery", "Sparkle for every day", "necklace", "cat-jewellery"),
    ("toys", "Toys", "Toys & Play", "Joy in every box", "teddy", "cat-toys"),
    ("kids", "Kids", "Kids Fashion", "Little steps, big smiles", "kidshoe", "cat-kids"),
]

BENEFITS = [
    ("truck", "Nationwide delivery", "From Dhaka to your doorstep"),
    ("shield-check", "Safe & gentle picks", "Child-friendly, carefully checked"),
    ("package-check", "Gift-ready packing", "Wrapped with love, every order"),
    ("headset", "Here to help", "Friendly support, always"),
]

# name, art, category, price, old, badge, rating, bg, swatches, stock
PRODUCTS = [
    ("Rose Quilted Handbag", "handbag", "bags", 2490, 3200, "BESTSELLER", "4.9", "#ffe3f0", ["#ff7eb6", "#b9a0ff", "#fff1d6"], 40),
    ("Blush Stiletto Heels", "heels", "shoes", 2190, 2790, "-21%", "4.8", "#ffe6e0", ["#ff4f9a", "#2b1650"], 30),
    ("Velvet Kiss Lipstick", "lipstick", "beauty", 590, 790, "NEW DROP", "4.7", "#f3e6ff", ["#ff4f9a", "#c91f73", "#ff9ccb"], 120),
    ("Lilac Twirl Party Dress", "dress", "women", 2890, 3590, "TRENDING", "4.9", "#ece4ff", ["#7a4dff", "#ffc83d"], 35),
    ("Cuddle Bear Plush Teddy", "teddy", "toys", 1290, 1590, "KIDS' FAVE", "4.9", "#fff1d6", ["#d9924f", "#ff9ccb"], 60),
    ("Rainbow Learning Blocks", "blocks", "toys", 990, 1290, "-23%", "4.8", "#e1f4ff", ["#ffc83d", "#4cb8ff", "#ff4f9a"], 80),
    ("Sky Sparkle Kids Sneakers", "kidshoe", "kids", 1490, 1890, "NEW DROP", "4.8", "#ddf7ee", ["#4cb8ff", "#ffc83d"], 50),
    ("Pearl Heart Necklace", "necklace", "jewellery", 1190, 1490, "HOT PICK", "4.7", "#fff0f6", ["#ffd25a", "#fff"], 70),
]


COLORS = {"Rose": "#ff7eb6", "Lilac": "#b9a0ff", "Cream": "#fff1d6", "Sky": "#8fd3ff", "Mint": "#8fe8cf"}
SIZES = ["26", "27", "28", "29", "30"]
# product slug -> list of (color, size, stock, price override)
VARIANTS = {
    "rose-quilted-handbag": [("Rose", None, 20, None), ("Lilac", None, 25, None), ("Cream", None, 15, 2590)],
    "sky-sparkle-kids-sneakers": [(c, s, 6, None) for c in ("Sky", "Mint") for s in SIZES[:4]],
}


# Original neutral dataset: the automated tests are written against it (python manage.py seed_catalog --dataset test)
T_CATEGORIES = [
    ("tech", "Tech", "Tech & Gadgets", "Clever little upgrades", "headphones", "cat-tech"),
    ("home", "Home", "Home & Living", "Cozy up your space", "lamp", "cat-home"),
    ("beauty", "Beauty", "Beauty & Care", "Everyday self-care", "bottle", "cat-beauty"),
    ("fashion", "Fashion", "Fashion Finds", "The style essentials", "bag", "cat-fashion"),
    ("lifestyle", "Lifestyle", "Lifestyle", "Live a little better", "sunglasses", "cat-lifestyle"),
    ("fitness", "Fitness", "Fitness & More", "Move your way", "watch", "cat-fitness"),
]

T_BENEFITS = [
    ("truck", "Nationwide delivery", "From Dhaka to your doorstep"),
    ("shield-check", "Shop with confidence", "Thoughtfully selected products"),
    ("package-check", "Easy order experience", "Track every order in your account"),
    ("headset", "Here to help", "Support that really cares"),
]

# name, art, category, price, old, badge, rating, bg, swatches, stock
T_PRODUCTS = [
    ("Studio Wireless Headphones", "headphones", "tech", 2490, 3200, "BESTSELLER", "4.9", "#eaf2ee", ["#497365", "#1c3129", "#d8dcd6"], 40),
    ("Everyday Classic Tote Bag", "bag", "fashion", 1290, 1590, "NEW DROP", "4.8", "#f2eae5", ["#cbb3a0", "#5e5a52", "#b4c4ac"], 60),
    ("Minimalist Table Lamp", "lamp", "home", 1890, 2400, "-21%", "4.7", "#f5f2e9", ["#e5dec9", "#9eae98"], 25),
    ("Smart Active Watch", "watch", "fitness", 3490, 4200, "HOT PICK", "4.9", "#edf2f5", ["#273f38", "#a8b9b2"], 30),
    ("Vacuum Insulated Tumbler", "bottle", "beauty", 890, 1190, "POPULAR", "4.8", "#e9f2ed", ["#78a792", "#ece7d8"], 100),
    ("Portable Bluetooth Speaker", "speaker", "tech", 2190, 2790, "-21%", "4.7", "#eaf1ed", ["#467b68", "#252e2b"], 35),
    ("Cloudstep Everyday Sneakers", "sneaker", "fashion", 2890, 3590, "NEW DROP", "4.9", "#f6f0ea", ["#eae9df", "#b6cebc"], 50),
    ("Retro Everyday Sunglasses", "sunglasses", "lifestyle", 690, 890, "TRENDING", "4.6", "#f1f1e8", ["#3e342f", "#b7aa9c"], 80),
]


T_COLORS = {"Sand": "#cbb3a0", "Charcoal": "#5e5a52", "Sage": "#b4c4ac", "Cloud": "#eae9df", "Mint": "#b6cebc"}
T_SIZES = ["40", "41", "42", "43", "44"]
# product slug -> list of (color, size, stock, price override)
T_VARIANTS = {
    "everyday-classic-tote-bag": [("Sand", None, 20, None), ("Charcoal", None, 25, None), ("Sage", None, 15, 1390)],
    "cloudstep-everyday-sneakers": [(c, s, 6, None) for c in ("Cloud", "Mint") for s in T_SIZES[:4]],
}


class Command(BaseCommand):
    help = "Seed the demo categories and products (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dataset", choices=["narizmart", "test"], default="narizmart")

    def handle(self, *args, **options):
        if options["dataset"] == "test":
            categories, benefits, products, colors, sizes, variants = T_CATEGORIES, T_BENEFITS, T_PRODUCTS, T_COLORS, T_SIZES, T_VARIANTS
        else:
            categories, benefits, products, colors, sizes, variants = CATEGORIES, BENEFITS, PRODUCTS, COLORS, SIZES, VARIANTS
        cats = {}
        for pos, (slug, name, title, desc, art, css) in enumerate(categories):
            cats[slug], _ = Category.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "title": title, "description": desc, "art_type": art, "css_class": css, "position": pos},
            )
        for i, (name, art, cat, price, old, badge, rating, bg, swatches, stock) in enumerate(products, 1):
            Product.objects.update_or_create(
                slug=slugify(name),
                defaults={
                    "category": cats[cat], "name": name, "sku": f"NM-{i:04d}", "art_type": art,
                    "price": Decimal(price), "old_price": Decimal(old), "badge": badge,
                    "rating": Decimal(rating), "bg_color": bg, "swatches": swatches, "stock": stock,
                    "description": "Soft, bright and made to be loved. Carefully chosen for ladies and little ones.",
                },
            )
        for pos, (name, hex_code) in enumerate(colors.items()):
            Color.objects.update_or_create(name=name, defaults={"hex_code": hex_code, "position": pos})
        for pos, name in enumerate(sizes):
            Size.objects.update_or_create(name=name, defaults={"position": pos})
        for slug, rows in variants.items():
            product = Product.objects.get(slug=slug)
            for color, size, stock, price in rows:
                ProductVariant.objects.update_or_create(
                    product=product, color=Color.objects.get(name=color), size=Size.objects.get(name=size) if size else None,
                    defaults={"stock": stock, "price": price, "is_active": True},
                )
        SiteSettings.load()
        for pos, (icon, title, text) in enumerate(benefits):
            Benefit.objects.get_or_create(title=title, defaults={"icon": icon, "text": text, "position": pos})
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(categories)} categories, {len(products)} products"))
        self._default_banners()

    @staticmethod
    def _default_banners() -> None:
        """A first hero slide and promo banner for a brand-new shop (an existing shop already has its own)."""
        site = SiteSettings.load()
        if not HeroSlide.objects.exists():
            HeroSlide.objects.create(
                site=site, position=0, eyebrow=site.hero_eyebrow, title=site.hero_title, highlight=site.hero_highlight, text=site.hero_text,
                button_label=site.hero_button_primary, button_url="#featured", button2_label=site.hero_button_secondary, button2_url="#categories",
                art=site.hero_art, proof_text=site.hero_proof_text, float_title="Fresh finds", float_text="Just for you",
            )
        if not PromoBanner.objects.exists():
            PromoBanner.objects.create(
                site=site, position=0, tag=site.promo_tag, title=site.promo_title, highlight=site.promo_highlight, text=site.promo_text,
                button_label=site.promo_button, button_url="#categories", art=site.promo_art,
            )
