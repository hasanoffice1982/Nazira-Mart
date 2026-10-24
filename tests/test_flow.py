import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product
from apps.orders.models import Order

User = get_user_model()
pytestmark = pytest.mark.django_db

CHECKOUT = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1, Road 2", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    from django.core.cache import cache

    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def user():
    return User.objects.create_user("a@example.com", "pass12345!x", first_name="A", email_verified=True)


def add(client, product, n=1):
    for _ in range(n):
        client.post(reverse("cart:add", args=[product.pk]))


def test_pages_render(client):
    for name in ("catalog:home", "catalog:shop", "core:offline", "core:manifest", "accounts:login", "accounts:register"):
        assert client.get(reverse(name)).status_code == 200
    assert client.get("/sw.js")["Service-Worker-Allowed"] == "/"
    p = Product.objects.first()
    assert client.get(p.get_absolute_url()).status_code == 200
    assert client.get(reverse("catalog:quick", args=[p.slug])).status_code == 200


def test_search_and_filter(client):
    assert b"Studio Wireless" in client.get("/shop/?q=headphones").content
    assert b"Table Lamp" not in client.get("/shop/?category=tech").content


def test_guest_cart_merges_on_login(client, user):
    p = Product.objects.first()
    add(client, p, 2)
    client.post(reverse("accounts:login"), {"username": "a@example.com", "password": "pass12345!x"})
    assert user.cart.items.get(product=p).quantity == 2


def test_cart_respects_stock(client):
    p = Product.objects.first()
    Product.objects.filter(pk=p.pk).update(stock=1)
    add(client, p, 3)
    assert b"Only 1 left" in client.post(reverse("cart:add", args=[p.pk])).headers["HX-Trigger"].encode()


def test_checkout_page_open_to_guests_but_needs_items(client):
    assert client.get(reverse("orders:checkout")).status_code == 302  # empty bag -> shop
    add(client, Product.objects.first())
    assert b"Place order" in client.get(reverse("orders:checkout")).content


def test_order_flow_and_ownership(client, user):
    client.force_login(user)
    p = Product.objects.get(art_type="headphones")
    stock = p.stock
    add(client, p, 2)
    resp = client.post(reverse("orders:checkout"), CHECKOUT)
    order = Order.objects.get()
    assert resp.status_code == 302
    assert order.total == p.price * 2 + 60 and order.items.get().unit_price == p.price
    p.refresh_from_db()
    assert p.stock == stock - 2
    assert client.get(order.get_absolute_url()).status_code == 200
    # cannot see someone else's order
    other = User.objects.create_user("b@example.com", "pass12345!x")
    client.force_login(other)
    assert client.get(order.get_absolute_url()).status_code == 404


def test_cancel_restores_stock(client, user):
    client.force_login(user)
    p = Product.objects.first()
    stock = p.stock
    add(client, p)
    client.post(reverse("orders:checkout"), CHECKOUT)
    order = Order.objects.get()
    client.post(reverse("orders:cancel", args=[order.number]))
    order.refresh_from_db(); p.refresh_from_db()
    assert order.status == "cancelled" and p.stock == stock
    assert order.history.count() == 2


def test_invalid_phone_rejected(client, user):
    client.force_login(user)
    add(client, Product.objects.first())
    client.post(reverse("orders:checkout"), {**CHECKOUT, "phone": "123"})
    assert not Order.objects.exists()


def test_wishlist_toggle(client, user):
    p = Product.objects.first()
    assert client.post(reverse("wishlist:toggle", args=[p.pk])).status_code == 204
    client.force_login(user)
    client.post(reverse("wishlist:toggle", args=[p.pk]))
    assert user.wishlist_items.count() == 1
    client.post(reverse("wishlist:toggle", args=[p.pk]))
    assert user.wishlist_items.count() == 0


def test_home_is_cms_driven(client):
    from apps.core.models import HeroSlide

    HeroSlide.objects.create(title="Brand new headline")
    body = client.get("/").content.decode()
    assert "Brand new headline" in body and "Nationwide delivery" in body


def test_newsletter(client):
    from apps.core.models import NewsletterSubscriber

    assert b"valid email" in client.post(reverse("core:newsletter"), {"email": "nope"}).content
    client.post(reverse("core:newsletter"), {"email": "Hi@Example.com"})
    client.post(reverse("core:newsletter"), {"email": "hi@example.com"})
    assert NewsletterSubscriber.objects.count() == 1


def test_live_search_suggest(client):
    assert b"Studio Wireless" in client.get(reverse("catalog:suggest"), {"q": "head"}).content
    assert client.get(reverse("catalog:suggest"), {"q": "h"}).content.strip() == b""


def test_uploaded_image_replaces_illustration(client, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = tmp_path
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9ba\xd4\x00\x00\x00\x00IEND\xaeB`\x82")
    p = Product.objects.first()
    p.image = SimpleUploadedFile("x.png", png, "image/png")
    p.save()
    assert b'class="media-img"' in client.get(p.get_absolute_url()).content


def test_staff_cannot_sign_in_to_storefront(client):
    User.objects.create_superuser("boss@example.com", "pass12345!x")
    resp = client.post(reverse("accounts:login"), {"username": "boss@example.com", "password": "pass12345!x"})
    assert resp.status_code == 200 and b"Staff accounts" in resp.content
    assert "_auth_user_id" not in client.session
    # admin site still works for staff
    client.force_login(User.objects.get(email="boss@example.com"))
    assert client.get(reverse("admin:index"))["Location"] == reverse("accounts:two_factor")  # staff owe the emailed code first
    session = client.session
    session["2fa_verified"] = True
    session.save()
    assert client.get(reverse("admin:index")).status_code == 200


def test_customer_cannot_enter_admin(client, user):
    client.force_login(user)
    assert client.get(reverse("admin:index")).status_code == 302


# ---- public order tracking -----------------------------------------------------------------------------------------
def _placed_order(django_user_model):
    from apps.catalog.models import Product
    from apps.orders.services import create_order

    user = django_user_model.objects.create_user("t@example.com", "pass12345!x", email_verified=True)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    return create_order(user=user, lines=[(p.pk, 0, 1)], data=CHECKOUT)


def test_track_page_shows_status_with_number_and_phone_without_login(client, django_user_model):
    order = _placed_order(django_user_model)
    page = client.get(reverse("orders:track"))
    assert page.status_code == 200 and "Track your order" in page.content.decode()
    resp = client.post(reverse("orders:track"), {"number": order.number.lower(), "phone": "+880 1712-345678"})
    html = resp.content.decode()
    assert resp.status_code == 200 and order.number in html and "Order placed" in html
    assert "House 1, Road 2" not in html and html.count("01712345678") == 1   # street address hidden; the phone appears only in the form's example text


def test_track_page_rejects_wrong_phone_or_unknown_number_with_the_same_message(client, django_user_model):
    order = _placed_order(django_user_model)
    wrong_phone = client.post(reverse("orders:track"), {"number": order.number, "phone": "01999999999"}).content.decode()
    unknown = client.post(reverse("orders:track"), {"number": "NM-0000", "phone": "01712345678"}).content.decode()
    assert "couldn&#x27;t find an order" in wrong_phone and "couldn&#x27;t find an order" in unknown
    assert f"Order {order.number}" not in wrong_phone and "Delivering to" not in wrong_phone


def test_track_page_shows_courier_tracking_link_from_the_supplier(client, django_user_model):
    from apps.supplier.models import SupplierOrder

    order = _placed_order(django_user_model)
    SupplierOrder.objects.create(order=order, partner_order_id=order.number, state="sent", courier_name="Steadfast", tracking_code="ST-1", tracking_link="https://steadfast.com.bd/track/ST-1")
    html = client.post(reverse("orders:track"), {"number": order.number, "phone": "01712345678"}).content.decode()
    assert "Steadfast" in html and 'href="https://steadfast.com.bd/track/ST-1"' in html


def test_footer_links_to_the_public_tracking_page(client):
    assert reverse("orders:track") in client.get("/").content.decode()


def test_signed_in_customer_sees_their_latest_order_on_the_track_page_without_typing(client, django_user_model):
    order = _placed_order(django_user_model)
    client.force_login(order.user)
    html = client.get(reverse("orders:track")).content.decode()
    assert f"Order {order.number}" in html and "Your most recent order" in html


def test_track_page_does_not_show_other_peoples_orders_to_guests_or_other_users(client, django_user_model):
    order = _placed_order(django_user_model)
    assert f"Order {order.number}" not in client.get(reverse("orders:track")).content.decode()
    other = django_user_model.objects.create_user("o2@example.com", "pass12345!x", email_verified=True)
    client.force_login(other)
    assert f"Order {order.number}" not in client.get(reverse("orders:track")).content.decode()


def test_track_link_is_in_the_menu_bar_and_the_mobile_menu(client):
    html = client.get("/").content.decode()
    assert html.count(reverse("orders:track")) >= 3   # menu bar, mobile menu, footer
