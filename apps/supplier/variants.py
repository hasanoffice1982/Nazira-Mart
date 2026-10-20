"""Turn the size and colour lists the supplier writes inside product descriptions into catalogue variants.

The supplier API has no structured variants: sizes appear as text ("সাইজ (Size): ১৬, ১৮, ২০ ... বয়স ...") and colours as
"Color: Red, Blue". We only create variants from lists that are clearly labelled, so a plain product stays plain.
"""
from __future__ import annotations

import re
from itertools import product as combos

from django.db import transaction

from apps.catalog.models import Color, ProductVariant, Size

MAX_OPTIONS = 12
BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
LIST_END = r"(?=বয়স|age\b|colou?r|color|রঙ|কালার|সাইজ|size|\n|$)"
SIZE_RE = re.compile(r"(?:সাইজ|size)[^:：\n]{0,30}[:：]\s*(.+?)" + LIST_END, re.I | re.S)
COLOR_RE = re.compile(r"(?:colou?rs?|রঙ|কালার)[^:：\n]{0,30}[:：]\s*(.+?)" + r"(?=বয়স|age\b|size|সাইজ|\n|$)", re.I | re.S)
SIZE_TOKEN = re.compile(r"^(?:\d{1,3}|xxs|xs|s|m|l|xl|xxl|xxxl|2xl|3xl|free ?size|free)$", re.I)


def _split(raw: str) -> list[str]:
    return [t.strip(" .।-–") for t in re.split(r"[,،/|।]|\s+and\s+|\s+ও\s+", raw) if t.strip(" .।-–")]


def parse_sizes(description: str) -> list[str]:
    for found in SIZE_RE.finditer((description or "").translate(BN_DIGITS)):
        tokens = [t.upper() if not t.isdigit() else t for t in _split(found.group(1)) if SIZE_TOKEN.match(t)]
        if tokens:
            return list(dict.fromkeys(tokens))[:MAX_OPTIONS]
    return []


BN_COLORS = {"লাল": "Red", "নীল": "Blue", "সবুজ": "Green", "কালো": "Black", "সাদা": "White", "হলুদ": "Yellow", "কমলা": "Orange", "বেগুনি": "Purple",
             "গোলাপি": "Pink", "বাদামি": "Brown", "ধূসর": "Grey", "মেরুন": "Maroon", "অফ হোয়াইট": "Off White"}   # shop colours are always English
KNOWN_COLORS = {
    "red", "blue", "green", "black", "white", "pink", "yellow", "orange", "purple", "violet", "brown", "grey", "gray", "navy",
    "maroon", "cream", "beige", "gold", "golden", "silver", "sky blue", "off white", "off-white", "teal", "mint", "peach", "lavender",
    "লাল", "নীল", "সবুজ", "কালো", "সাদা", "গোলাপি", "হলুদ", "কমলা", "বেগুনি", "বাদামি", "ধূসর", "মেরুন", "অফ হোয়াইট",
}


def _english_color(name: str) -> str:
    return BN_COLORS.get(name.strip(), name.strip().title())


def parse_colors(description: str) -> list[str]:
    """Only well-known colour names count: Bangla descriptions contain many 'রঙ' sentences that are not option lists."""
    for found in COLOR_RE.finditer(description or ""):
        tokens = [_english_color(t) for t in _split(found.group(1)) if t.lower() in KNOWN_COLORS]
        if tokens:
            return list(dict.fromkeys(tokens))[:MAX_OPTIONS]
    return []


def parse_variant_field(text: str) -> tuple[list[str], list[str]]:
    """The API's structured field, e.g. "Color: Black, White | Size: M, L | Dial: 44mm". Only colour and size options are used."""
    sizes: list[str] = []
    colors: list[str] = []
    for part in str(text or "").split("|"):
        key, _, values = part.partition(":")
        key, options = key.strip().lower(), _split(values)
        if key in ("color", "colour", "রঙ", "কালার"):
            colors += [_english_color(o) for o in options if 2 <= len(o) <= 20]
        elif key in ("size", "সাইজ"):
            sizes += [o.upper() if not o.isdigit() else o for o in options if len(o) <= 20]
    return list(dict.fromkeys(sizes))[:MAX_OPTIONS], list(dict.fromkeys(colors))[:MAX_OPTIONS]


@transaction.atomic
def create_variants(product, description: str, stock: int, variants_text: str = "") -> int:
    """Create one variant per colour x size. The API's variants field wins; otherwise the description is searched. Returns how many were created."""
    sizes, colors = parse_variant_field(variants_text)
    if not sizes and not colors:
        sizes, colors = parse_sizes(description), parse_colors(description)
    if not sizes and not colors:
        return 0
    size_objs = [Size.objects.get_or_create(name=s, defaults={"position": n})[0] for n, s in enumerate(sizes)]
    color_objs = [Color.objects.get_or_create(name=c, defaults={"position": n})[0] for n, c in enumerate(colors)]
    created = 0
    for pos, (color, size) in enumerate(combos(color_objs or [None], size_objs or [None])):
        _, new = ProductVariant.objects.get_or_create(
            product=product, color=color, size=size, defaults={"stock": max(stock, 0), "position": pos},
        )
        created += new
    return created


def refresh_stock(product, stock: int) -> None:
    """Variants share the supplier's single stock figure, so keep them in step with it."""
    product.variants.update(stock=max(stock, 0))
