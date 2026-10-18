from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.orders.models import Order, Promotion
from apps.orders.promotions import evaluate
from apps.orders.services import OrderError, change_status, create_order

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


def promo(**kw):
    base = dict(name="10% off orders over 5,000", kind="percent", value=D("10"), min_subtotal=D("5000"))
    return Promotion.objects.create(**{**base, **kw})


def order(user, slug="smart-active-watch", qty=2, code=""):
    p = Product.objects.get(slug=slug)
    return create_order(user=user, lines=[(p.pk, 0, qty)], data=DELIVERY, coupon_code=code)


# ---- the campaign from the brief: 10% off when the order is at least 5,000 -----------------------------------------
def test_ten_percent_off_from_5000(buyer):
    promo()
    o = order(buyer)  # 2 x 3,490 = 6,980
    assert o.subtotal == D("6980") and o.discount == D("698.00") and o.promotion_label.startswith("10%")
    assert o.total == o.subtotal - o.discount + o.delivery_charge


def test_no_discount_below_the_minimum_and_a_nudge_is_shown():
    promo()
    deal = evaluate(D("4000"))
    assert deal.amount == 0 and deal.promotion is None and "1,000" in deal.nudge and "10% off" in deal.nudge


def test_exactly_the_minimum_qualifies():
    promo(min_subtotal=D("3490"))
    assert evaluate(D("3490")).amount == D("349.00")


def test_free_delivery_still_uses_the_products_total_before_discount(buyer):
    promo(value=D("50"))  # a 50% discount would drop 6,980 below the 5,000 free-delivery line
    o = order(buyer)
    assert o.delivery_charge == 0 and o.discount == D("3490.00")


# ---- coupon codes -------------------------------------------------------------------------------------------------
def test_coupon_code_applies_and_is_case_insensitive(buyer):
    promo(name="Eid campaign", code="eid10", min_subtotal=D("0"))
    o = order(buyer, "retro-everyday-sunglasses", 1, code="  Eid10 ")
    assert o.coupon_code == "EID10" and o.discount == D("69.00") and o.promotion_label == "Eid campaign"


@pytest.mark.parametrize("make,message", [
    (lambda: None, "isn't valid"),
    (lambda: promo(code="OFF", is_active=False), "isn't valid"),
    (lambda: promo(code="OFF", ends_at=timezone.now() - timedelta(days=1)), "expired"),
    (lambda: promo(code="OFF", starts_at=timezone.now() + timedelta(days=1)), "isn't valid"),
    (lambda: promo(code="OFF", min_subtotal=D("9999")), "more to use this code"),
])
def test_bad_coupons_are_rejected_with_a_reason(buyer, make, message):
    make()
    with pytest.raises(OrderError) as exc:
        order(buyer, code="OFF")
    assert message in str(exc.value)
    assert not Order.objects.exists()  # nothing created, stock untouched
    assert Product.objects.get(slug="smart-active-watch").stock == 30


def test_fixed_amount_never_exceeds_the_order_and_cap_applies(buyer):
    promo(code="BIG", kind="fixed", value=D("100000"), min_subtotal=D("0"))
    assert order(buyer, "retro-everyday-sunglasses", 1, code="BIG").discount == D("690.00")
    promo(code="CAPPED", name="Capped", value=D("50"), min_subtotal=D("0"), max_discount=D("100"))
    assert evaluate(D("6980"), buyer, "CAPPED").amount == D("100.00")


def test_usage_limits_total_and_per_customer(buyer):
    promo(code="ONCE", min_subtotal=D("0"), per_customer_limit=1, usage_limit=2)
    first = order(buyer, "retro-everyday-sunglasses", 1, code="ONCE")
    with pytest.raises(OrderError, match="already used"):
        order(buyer, "retro-everyday-sunglasses", 1, code="ONCE")
    other = User.objects.create_user("b2@example.com", "pass12345!x", email_verified=True)
    order(other, "retro-everyday-sunglasses", 1, code="ONCE")
    third = User.objects.create_user("b3@example.com", "pass12345!x", email_verified=True)
    with pytest.raises(OrderError, match="fully claimed"):
        order(third, "retro-everyday-sunglasses", 1, code="ONCE")
    change_status(first, Order.Status.CANCELLED)  # a cancelled order gives the use back
    assert order(third, "retro-everyday-sunglasses", 1, code="ONCE").discount > 0


def test_best_single_discount_wins_and_does_not_stack(buyer):
    promo()                                                       # automatic 10% = 698
    promo(code="FLAT500", name="Flat 500", kind="fixed", value=D("500"), min_subtotal=D("0"))
    o = order(buyer, code="FLAT500")
    assert o.discount == D("698.00") and o.coupon_code == ""      # automatic is better; the code isn't consumed
    promo(code="MEGA", name="Mega", value=D("20"), min_subtotal=D("0"))
    o2 = order(buyer, code="MEGA")
    assert o2.discount == D("1396.00") and o2.coupon_code == "MEGA"


def test_note_when_the_automatic_discount_is_already_better():
    promo()
    promo(code="SMALL", name="Small", kind="fixed", value=D("10"), min_subtotal=D("0"))
    assert "wasn't needed" in evaluate(D("6980"), None, "SMALL").coupon_note


# ---- model rules --------------------------------------------------------------------------------------------------
def test_codes_are_unique_ignoring_case():
    promo(code="SAVE")
    with pytest.raises(IntegrityError):
        promo(code="save")


def test_validation():
    with pytest.raises(ValidationError):
        Promotion(name="x", kind="percent", value=D("150")).full_clean()
    now = timezone.now()
    with pytest.raises(ValidationError):
        Promotion(name="x", value=D("5"), starts_at=now, ends_at=now - timedelta(hours=1)).full_clean()


# ---- checkout pages -----------------------------------------------------------------------------------------------
def fill_bag(client, slug="smart-active-watch", qty=2):
    p = Product.objects.get(slug=slug)
    for _ in range(qty):
        client.post(reverse("cart:add", args=[p.pk]))


def test_checkout_shows_automatic_discount_and_coupon_box(client):
    promo()
    fill_bag(client)
    html = client.get(reverse("orders:checkout")).content.decode()
    assert "698" in html and "10% off orders over 5,000" in html and 'name="code"' in html


def test_checkout_nudges_when_below_minimum(client):
    promo()
    fill_bag(client, "retro-everyday-sunglasses", 1)
    assert "more to get" in client.get(reverse("orders:checkout")).content.decode()


def test_apply_and_remove_coupon_on_checkout(client):
    promo(code="SAVE5", name="Save five", value=D("5"), min_subtotal=D("0"))
    fill_bag(client, "retro-everyday-sunglasses", 1)
    page = client.post(reverse("orders:coupon_apply"), {"code": "save5"}, follow=True).content.decode()
    assert "SAVE5" in page and "applied" in page and "34.50" in page
    assert "Remove" in page
    after = client.post(reverse("orders:coupon_remove"), follow=True).content.decode()
    assert "Coupon code" in after and "34.50" not in after


def test_invalid_code_shows_an_error_and_is_not_kept(client):
    fill_bag(client, "retro-everyday-sunglasses", 1)
    page = client.post(reverse("orders:coupon_apply"), {"code": "NOPE"}, follow=True).content.decode()
    assert "isn&#x27;t valid" in page or "isn't valid" in page
    assert client.session.get("coupon_code") is None


def test_signed_in_customer_places_order_with_coupon_and_sees_it(client, buyer):
    promo(code="SAVE5", name="Save five", value=D("5"), min_subtotal=D("0"))
    client.force_login(buyer)
    fill_bag(client, "retro-everyday-sunglasses", 1)
    client.post(reverse("orders:coupon_apply"), {"code": "SAVE5"})
    resp = client.post(reverse("orders:checkout"), {**DELIVERY})
    placed = Order.objects.get()
    assert resp.status_code == 302 and placed.coupon_code == "SAVE5" and placed.discount == D("34.50")
    assert "coupon_code" not in client.session
    page = client.get(placed.get_absolute_url()).content.decode()
    assert "Save five" in page and "34.50" in page
    assert "Save five" in mail.outbox[-1].body and "34.50" in mail.outbox[-1].body


def test_coupon_survives_the_guest_email_code_step(client):
    import re

    promo(code="SAVE5", name="Save five", value=D("5"), min_subtotal=D("0"))
    fill_bag(client, "retro-everyday-sunglasses", 1)
    client.post(reverse("orders:coupon_apply"), {"code": "SAVE5"})
    client.post(reverse("orders:checkout"), {"email": "guest@example.com", **DELIVERY})
    code = re.search(r"\n(\d{6})\n", mail.outbox[-1].body).group(1)
    client.post(reverse("orders:confirm"), {"code": code})
    placed = Order.objects.get()
    assert placed.coupon_code == "SAVE5" and placed.discount == D("34.50")


def test_discount_row_hidden_when_there_is_none(buyer):
    o = order(buyer, "retro-everyday-sunglasses", 1)
    from django.test import Client

    c = Client()
    c.force_login(buyer)
    assert "sum-discount" not in c.get(o.get_absolute_url()).content.decode()   # the order summary has no discount row


def test_admin_can_manage_promotions(admin_client):
    from apps.accounts.twofactor import VERIFIED_KEY

    s = admin_client.session
    s[VERIFIED_KEY] = True
    s.save()
    promo(code="LIVE", name="Live one")
    r = admin_client.get(reverse("admin:orders_promotion_changelist"))
    assert r.status_code == 200 and b"Live one" in r.content and b"LIVE" in r.content
    assert admin_client.get(reverse("admin:orders_promotion_add")).status_code == 200


def test_cart_drawer_shows_nudge_then_discount(client):
    promo()
    fill_bag(client, "retro-everyday-sunglasses", 1)
    page = client.get("/").content.decode()
    assert "more to get 10% off" in page
    fill_bag(client, "smart-active-watch", 2)
    page = client.get("/").content.decode()
    assert "drawer-discount" in page and "10% off orders over 5,000" in page
