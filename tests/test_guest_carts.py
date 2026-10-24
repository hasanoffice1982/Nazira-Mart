import csv
import io
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.cart.models import GuestCart
from apps.catalog.models import Product
from apps.orders.models import Order

User = get_user_model()
pytestmark = pytest.mark.django_db
FORM = {"email": "Guest@Example.com", "full_name": "Guest Buyer", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def product():
    return Product.objects.get(art_type="headphones")


def add(client, product, times=1):
    for _ in range(times):
        client.post(reverse("cart:add", args=[product.pk]))


def test_a_guest_bag_is_saved_with_the_session_id(client, product):
    add(client, product, 2)
    bag = GuestCart.objects.get()
    assert bag.session_key == client.session.session_key
    assert bag.items == [[product.pk, 0, 2]] and bag.item_count == 2 and not bag.has_contact


def test_changing_the_bag_updates_the_same_row(client, product):
    add(client, product)
    add(client, product)
    client.post(reverse("cart:qty", args=[product.pk]), {"delta": "-1"})
    assert GuestCart.objects.count() == 1 and GuestCart.objects.get().item_count == 1


def test_an_emptied_bag_without_contact_details_is_forgotten(client, product):
    add(client, product)
    client.post(reverse("cart:remove", args=[product.pk]))
    assert not GuestCart.objects.exists()


def test_signed_in_customers_do_not_create_guest_bags(client, product):
    client.force_login(User.objects.create_user("c@example.com", "pass12345!x", email_verified=True))
    add(client, product)
    assert not GuestCart.objects.exists()


def test_checkout_autosave_stores_what_the_visitor_typed(client, product):
    add(client, product)
    resp = client.post(reverse("cart:contact"), {"name": "Rafi", "email": "Rafi@Example.com", "phone": "017 1234-5678"})
    assert resp.status_code == 204
    bag = GuestCart.objects.get()
    assert (bag.name, bag.email, bag.phone) == ("Rafi", "rafi@example.com", "01712345678") and bag.has_contact


def test_autosave_ignores_a_bad_email_and_a_short_phone_and_an_empty_bag(client, product):
    client.post(reverse("cart:contact"), {"email": "nope", "phone": "123"})
    assert not GuestCart.objects.exists()
    add(client, product)
    client.post(reverse("cart:contact"), {"email": "nope", "phone": "123"})
    bag = GuestCart.objects.get()
    assert bag.email == "" and bag.phone == ""


def test_autosave_rebuilds_the_row_for_a_bag_that_was_never_tracked(client, product):
    session = client.session
    session["cart"] = {f"{product.pk}:0": 3}   # a bag from before tracking existed
    session.save()
    client.post(reverse("cart:contact"), {"phone": "01712345678"})
    bag = GuestCart.objects.get()
    assert bag.items == [[product.pk, 0, 3]] and bag.item_count == 3 and bag.phone == "01712345678"


def test_a_bag_with_contact_details_is_kept_when_the_bag_is_emptied(client, product):
    add(client, product)
    client.post(reverse("cart:contact"), {"phone": "01712345678"})
    client.post(reverse("cart:remove", args=[product.pk]))
    bag = GuestCart.objects.get()
    assert bag.item_count == 0 and bag.phone == "01712345678"


def test_placing_the_order_links_the_bag_to_it(client, product):
    add(client, product)
    client.post(reverse("cart:contact"), {"phone": "01799999999"})
    client.post(reverse("orders:checkout"), FORM)
    order = Order.objects.get()
    bag = GuestCart.objects.get()
    assert bag.order == order and bag.item_count == 0
    assert bag.email == "guest@example.com" and bag.phone == "01712345678" and bag.name == "Guest Buyer"


def test_a_new_bag_after_an_order_is_a_new_row(client, product):
    add(client, product)
    client.post(reverse("orders:checkout"), FORM)
    client.logout()
    add(client, product)
    assert GuestCart.objects.count() == 2


def test_signing_in_removes_the_guest_bag(client, product):
    User.objects.create_user("c@example.com", "pass12345!x", email_verified=True)
    add(client, product)
    assert GuestCart.objects.count() == 1
    client.post(reverse("accounts:login"), {"username": "c@example.com", "password": "pass12345!x"})
    assert not GuestCart.objects.exists()


def test_autosave_is_ignored_for_signed_in_customers(client, product):
    add(client, product)
    client.force_login(User.objects.create_user("c@example.com", "pass12345!x", email_verified=True))   # signing in folds the guest bag into the account
    assert client.post(reverse("cart:contact"), {"email": "x@example.com"}).status_code == 204
    assert not GuestCart.objects.exists()


def test_checkout_page_saves_contact_details_and_says_so(client, product):
    add(client, product)
    html = client.get(reverse("orders:checkout")).content
    assert b"js/checkout-capture.js" in html and b"we may contact you about your bag" in html


def test_admin_lists_follow_up_bags_with_links_and_exports_csv(client, product):
    add(client, product, 2)
    client.post(reverse("cart:contact"), {"name": "Rafi", "email": "rafi@example.com", "phone": "01712345678"})
    from django.test import Client

    from apps.accounts.twofactor import VERIFIED_KEY

    client = Client()   # a separate browser for the staff member, so the visitor's bag is untouched
    client.force_login(User.objects.create_superuser("boss@example.com", "pass12345!x"))
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    page = client.get(reverse("admin:cart_guestcart_changelist") + "?outcome=left&contact=yes")
    assert page.status_code == 200 and b"wa.me/8801712345678" in page.content and b"mailto:rafi@example.com" in page.content
    assert product.name.encode() in page.content
    out = client.post(reverse("admin:cart_guestcart_changelist"), {"action": "export_csv", "_selected_action": [GuestCart.objects.get().pk]})
    rows = list(csv.reader(io.StringIO(out.content.decode())))
    assert rows[1][1:4] == ["Rafi", "01712345678", "rafi@example.com"] and "× 2" in rows[1][4]
    assert client.get(reverse("admin:cart_guestcart_add")).status_code == 403


def test_old_bags_are_purged(client, product):
    add(client, product)
    GuestCart.objects.update(updated_at=timezone.now() - timedelta(days=61))
    other = GuestCart.objects.create(session_key="fresh", items=[[product.pk, 0, 1]], item_count=1)
    call_command("purge_guest_carts")
    assert list(GuestCart.objects.all()) == [other]
