import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command

from apps.catalog.models import Product
from apps.catalog.popular import popular_products
from apps.core.models import SiteSettings
from apps.orders.models import Order
from apps.orders.services import change_status, create_order

User = get_user_model()
pytestmark = pytest.mark.django_db
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def buyer():
    return User.objects.create_user("buyer@example.com", "pass12345!x", email_verified=True)


def site(**kw):
    s = SiteSettings.load_fresh()
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()
    return SiteSettings.load_fresh()


def buy(user, slug, qty):
    p = Product.objects.get(slug=slug)
    return create_order(user=user, lines=[(p.pk, 0, qty)], data=DELIVERY)


def slugs(s):
    return [p.slug for p in popular_products(s)]


def test_before_any_sale_shows_four_newest():
    result = popular_products(site())
    assert len(result) == 4
    assert [p.id for p in result] == list(Product.objects.order_by("-created_at", "-id").values_list("id", flat=True)[:4])


def test_best_sellers_take_over_once_there_are_sales(buyer):
    buy(buyer, "retro-everyday-sunglasses", 5)
    buy(buyer, "minimalist-table-lamp", 3)
    buy(buyer, "smart-active-watch", 1)
    s = site()
    result = slugs(s)
    assert result[:3] == ["retro-everyday-sunglasses", "minimalist-table-lamp", "smart-active-watch"]
    assert len(result) == 6  # grows to the "once there are sales" size, filled with newest


def test_pinned_products_come_first_in_mix_mode(buyer):
    buy(buyer, "retro-everyday-sunglasses", 5)
    pin = Product.objects.get(slug="studio-wireless-headphones")
    pin.is_popular_pick = True
    pin.save()
    assert slugs(site())[:2] == ["studio-wireless-headphones", "retro-everyday-sunglasses"]


def test_manual_mode_shows_only_pinned(buyer):
    buy(buyer, "retro-everyday-sunglasses", 5)
    for slug, order in (("minimalist-table-lamp", 2), ("smart-active-watch", 1)):
        p = Product.objects.get(slug=slug)
        p.is_popular_pick, p.popular_order = True, order
        p.save()
    assert slugs(site(popular_mode="manual")) == ["smart-active-watch", "minimalist-table-lamp"]


def test_auto_mode_ignores_pins(buyer):
    buy(buyer, "retro-everyday-sunglasses", 2)
    p = Product.objects.get(slug="smart-active-watch")
    p.is_popular_pick = True
    p.save()
    assert slugs(site(popular_mode="auto"))[0] == "retro-everyday-sunglasses"


def test_cancelled_orders_and_inactive_products_do_not_count(buyer):
    order = buy(buyer, "retro-everyday-sunglasses", 9)
    change_status(order, Order.Status.CANCELLED)
    buy(buyer, "minimalist-table-lamp", 1)
    assert slugs(site())[0] == "minimalist-table-lamp"
    lamp = Product.objects.get(slug="minimalist-table-lamp")
    lamp.is_active = False
    lamp.save()
    assert "minimalist-table-lamp" not in slugs(site())


def test_count_setting_and_toggle(buyer):
    buy(buyer, "retro-everyday-sunglasses", 1)
    assert len(slugs(site(popular_count=5))) == 5
    assert popular_products(site(show_popular=False)) == []


def test_home_page_renders_section_and_heading(client, buyer):
    buy(buyer, "retro-everyday-sunglasses", 4)
    site(popular_title="Hot right now", popular_label="TOP PICKS")
    html = client.get("/").content.decode()
    assert 'id="popular"' in html and "Hot right now" in html and "TOP PICKS" in html and "Retro Everyday Sunglasses" in html
    site(show_popular=False)
    assert 'id="popular"' not in client.get("/").content.decode()


def test_pin_change_is_visible_immediately():
    p = Product.objects.get(slug="smart-active-watch")
    assert p.slug not in slugs(site(popular_fallback_count=1))
    p.is_popular_pick = True
    p.save()
    assert slugs(site(popular_fallback_count=1)) == ["smart-active-watch"]


def test_demo_photos_command_is_idempotent(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    call_command("seed_demo_photos")
    first = Product.objects.get(slug="studio-wireless-headphones")
    assert first.images.count() == 2 and all(i.image.name.endswith(".webp") for i in first.images.all())
    total = sum(p.images.count() for p in Product.objects.all())
    call_command("seed_demo_photos")
    assert sum(p.images.count() for p in Product.objects.all()) == total
