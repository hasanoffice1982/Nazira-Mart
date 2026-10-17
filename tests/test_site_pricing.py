import importlib
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.forms.models import model_to_dict
from django.urls import reverse

from apps.cart.services import delivery_charge
from apps.catalog.models import Product
from apps.core.models import Page, SiteSettings
from apps.orders.models import Order
from apps.orders.services import change_status, create_order
from tests.helpers import inline_management

User = get_user_model()
pytestmark = pytest.mark.django_db
D = Decimal
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


def set_prices(**kw):
    s = SiteSettings.load_fresh()
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()
    return s


@pytest.fixture
def staff_client(client):
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    s = client.session; s["2fa_verified"] = True; s.save()
    return client


# ---- defaults keep today's behaviour -----------------------------------------------------
def test_defaults_match_the_old_hard_coded_values():
    s = SiteSettings.load_fresh()
    assert (s.currency_symbol, s.delivery_charge_dhaka, s.delivery_charge_outside, s.free_delivery_threshold) == ("৳", 60, 120, 5000)
    assert delivery_charge(D(1000), "dhaka") == 60 and delivery_charge(D(1000), "outside") == 120 and delivery_charge(D(5000)) == 0


# ---- rules ---------------------------------------------------------------------------------------
def test_free_delivery_boundary_is_inclusive():
    set_prices(free_delivery_threshold=D("2000"))
    assert delivery_charge(D("1999.99"), "dhaka") == 60
    assert delivery_charge(D("2000"), "dhaka") == 0 and delivery_charge(D("2000"), "outside") == 0


def test_threshold_zero_turns_free_delivery_off():
    set_prices(free_delivery_threshold=D("0"))
    assert delivery_charge(D("999999"), "outside") == 120 and delivery_charge(D("1"), "dhaka") == 60


def test_empty_bag_costs_nothing_to_deliver():
    assert delivery_charge(D(0)) == 0


def test_zero_charge_areas_are_allowed():
    set_prices(delivery_charge_dhaka=D("0"))
    assert delivery_charge(D(100), "dhaka") == 0 and delivery_charge(D(100), "outside") == 120


def test_unknown_area_is_charged_as_outside_dhaka():
    assert delivery_charge(D(100), "mars") == 120


def test_negative_prices_are_rejected():
    s = SiteSettings.load_fresh()
    for field in ("delivery_charge_dhaka", "delivery_charge_outside", "free_delivery_threshold"):
        setattr(s, field, D("-1"))
        with pytest.raises(ValidationError):
            s.full_clean()
        setattr(s, field, D("10"))


# ---- changes apply immediately, in every worker ------------------------------------------------------
def test_pricing_ignores_a_stale_cache():
    SiteSettings.load()  # warms the cache like another worker would have
    SiteSettings.objects.filter(pk=1).update(delivery_charge_dhaka=D("99"))  # changed behind the cache's back
    assert delivery_charge(D(100), "dhaka") == 99


def test_new_charge_is_used_for_the_next_order_but_old_orders_keep_theirs():
    user = User.objects.create_user("buyer@example.com", "pass12345!x", email_verified=True)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    first = create_order(user=user, lines=[(p.pk, 0, 1)], data=DELIVERY)
    assert first.delivery_charge == 60
    set_prices(delivery_charge_dhaka=D("85"))
    second = create_order(user=user, lines=[(p.pk, 0, 1)], data=DELIVERY)
    first.refresh_from_db()
    assert second.delivery_charge == 85 and second.total == p.price + 85
    assert first.delivery_charge == 60  # history is never rewritten


def test_checkout_page_shows_the_admin_prices(client):
    set_prices(delivery_charge_dhaka=D("45"), delivery_charge_outside=D("99.50"))
    client.post(reverse("cart:add", args=[Product.objects.get(slug="retro-everyday-sunglasses").pk]))
    html = client.get(reverse("orders:checkout")).content.decode()
    assert "৳45" in html and "৳99.50" in html  # decimals are shown, never rounded to 100


# ---- currency symbol -------------------------------------------------------------------------------------
def test_currency_symbol_follows_admin_everywhere(client):
    set_prices(currency_symbol="Tk ")
    html = client.get("/").content.decode()
    assert "Tk " in html and "৳" not in html.split("<main")[1]  # prices on the page use the new symbol


def test_currency_symbol_in_order_emails():
    set_prices(currency_symbol="BDT ")
    user = User.objects.create_user("buyer@example.com", "pass12345!x", email_verified=True)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    order = create_order(user=user, lines=[(p.pk, 0, 1)], data=DELIVERY)
    mail.outbox.clear()
    from apps.orders.notifications import send_status_email

    send_status_email(order.pk, "confirmed")
    assert "BDT " in mail.outbox[0].body and "৳" not in mail.outbox[0].body
    assert change_status and Order.objects.count() == 1


# ---- tokens in copy ---------------------------------------------------------------------------------------
def test_announcement_and_shipping_page_follow_the_prices(client):
    set_prices(delivery_charge_dhaka=D("70"), delivery_charge_outside=D("130.50"), free_delivery_threshold=D("7500"))
    home = client.get("/").content.decode()
    assert "Free delivery over ৳7,500" in home and "[[" not in home
    page = client.get(reverse("core:page", args=["shipping-delivery"])).content.decode()
    assert "Inside Dhaka: ৳70" in page and "Outside Dhaka: ৳130.50" in page and "৳7,500 or more" in page and "[[" not in page


def test_tokens_render_blank_when_free_delivery_is_off(client):
    set_prices(free_delivery_threshold=D("0"))
    assert "Free delivery over ৳" in client.get("/").content.decode()  # the admin is told to edit the text; no crash, no raw token


def test_unknown_brackets_in_page_text_are_left_alone(client):
    Page.objects.filter(slug="help-center").update(body="Call [[us]] on [[currency]]100")
    html = client.get(reverse("core:page", args=["help-center"])).content.decode()
    assert "[[us]]" in html and "৳100" in html


def test_money_formatting():
    f = SiteSettings._money
    assert (f(D(60)), f(D(5000)), f(D("99.5")), f(D("1234567"))) == ("60", "5,000", "99.50", "1,234,567")


# ---- existing copy is migrated safely -----------------------------------------------------------------------
def test_migration_converts_untouched_default_copy_only():
    migration = importlib.import_module("apps.core.migrations.0003_delivery_pricing")
    SiteSettings.objects.filter(pk=1).delete()
    SiteSettings.objects.create(pk=1, announcement=migration.OLD_ANNOUNCEMENT)
    Page.objects.filter(slug="shipping-delivery").update(body="## Delivery charges\n- Inside Dhaka: ৳60\n- Outside Dhaka: ৳999 (we changed this)\n- Free delivery on orders of ৳5,000 or more")
    migration.use_price_tokens(django_apps, None)
    assert SiteSettings.objects.get(pk=1).announcement == migration.NEW_ANNOUNCEMENT
    body = Page.objects.get(slug="shipping-delivery").body
    assert "[[delivery_dhaka]]" in body and "[[free_delivery]]" in body
    assert "৳999 (we changed this)" in body  # a line the owner edited is never overwritten


def test_migration_leaves_a_customised_announcement_alone():
    migration = importlib.import_module("apps.core.migrations.0003_delivery_pricing")
    SiteSettings.objects.filter(pk=1).update(announcement="Eid sale: everything 20% off!")
    migration.use_price_tokens(django_apps, None)
    assert SiteSettings.objects.get(pk=1).announcement == "Eid sale: everything 20% off!"


# ---- the admin screen --------------------------------------------------------------------------------------------
def admin_post(client, **changes):
    site = SiteSettings.load_fresh()
    data = {k: ("" if v is None else v) for k, v in model_to_dict(site).items() if k not in ("id", "hero_image", "promo_image", "story_image")}
    data.update(inline_management())
    data.update(changes)
    return client.post(reverse("admin:core_sitesettings_change", args=[1]), data)


def test_admin_screen_shows_the_pricing_fields(staff_client):
    SiteSettings.load_fresh()
    html = staff_client.get(reverse("admin:core_sitesettings_change", args=[1])).content.decode()
    for name in ("currency_symbol", "delivery_charge_dhaka", "delivery_charge_outside", "free_delivery_threshold"):
        assert f'name="{name}"' in html
    assert "Currency &amp; delivery pricing" in html


def test_staff_can_change_prices_in_admin_and_checkout_uses_them(staff_client):
    resp = admin_post(staff_client, delivery_charge_dhaka="75", delivery_charge_outside="150", free_delivery_threshold="4000", currency_symbol="Tk")
    assert resp.status_code == 302
    assert (delivery_charge(D(1000), "dhaka"), delivery_charge(D(1000), "outside"), delivery_charge(D(4000))) == (75, 150, 0)
    assert SiteSettings.load_fresh().currency_symbol == "Tk"


def test_admin_rejects_negative_or_non_numeric_prices(staff_client):
    for bad in ("-5", "abc"):
        resp = admin_post(staff_client, delivery_charge_dhaka=bad)
        assert resp.status_code == 200  # form re-shown with an error, nothing saved
    assert SiteSettings.load_fresh().delivery_charge_dhaka == 60


def test_customers_cannot_open_the_pricing_screen(client):
    SiteSettings.load_fresh()
    u = User.objects.create_user("c@example.com", "pass12345!x", email_verified=True)
    client.force_login(u)
    assert client.get(reverse("admin:core_sitesettings_change", args=[1])).status_code == 302


# ---- decimals are displayed exactly everywhere -------------------------------------------------------------------
def test_fractional_delivery_charge_is_shown_exactly_in_orders_and_emails():
    set_prices(delivery_charge_dhaka=D("99.50"))
    user = User.objects.create_user("buyer@example.com", "pass12345!x", email_verified=True)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    order = create_order(user=user, lines=[(p.pk, 0, 1)], data=DELIVERY)
    assert order.delivery_charge == D("99.50") and order.total == p.price + D("99.50")
    from django.test import Client

    c = Client()
    c.force_login(user)
    assert "৳99.50" in c.get(order.get_absolute_url()).content.decode()  # not rounded to ৳100
    from apps.orders.notifications import send_status_email

    mail.outbox.clear()
    send_status_email(order.pk, "confirmed")
    assert "99.50" in mail.outbox[0].body and "99.50" in mail.outbox[0].alternatives[0][0]


def test_whole_number_prices_stay_clean(client):
    html = client.get("/shop/").content.decode()
    assert "৳2,490" in html and "৳2,490.00" not in html
