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


def parse_colors(description: str) -> list[str]:
    for found in COLOR_RE.finditer(description or ""):
        tokens = [t.title() for t in _split(found.group(1)) if 2 <= len(t) <= 20 and not re.search(r"\d", t)]
        if tokens:
            return list(dict.fromkeys(tokens))[:MAX_OPTIONS]
    return []


@transaction.atomic
def create_variants(product, description: str, stock: int) -> int:
    """Create one variant per colour x size found in the description. Returns how many were created."""
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
