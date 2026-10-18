import pytest

from apps.catalog.models import CATEGORY_TINTS, Category, Product

pytestmark = pytest.mark.django_db


def make(cat_slug, **kw):
    cat, _ = Category.objects.get_or_create(slug=cat_slug, defaults={"name": cat_slug.title(), "title": cat_slug.title()})
    return Product.objects.create(category=cat, name=f"P {cat_slug}", slug=f"p-{cat_slug}", price=100, art_type="bag", **kw)


def test_default_background_follows_the_category():
    assert make("toys").card_bg == CATEGORY_TINTS["toys"]


def test_custom_background_wins():
    assert make("kids", bg_color="#123456").card_bg == "#123456"


def test_unknown_category_gets_a_soft_fallback_tint():
    assert make("gadgets").card_bg.startswith("#")
