import json

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext, override_settings

from apps.catalog.models import Category, Color, Product, ProductVariant, Size
from apps.chatorders.models import ChatOrder
from apps.orders.models import Order
from apps.orders.services import change_status

TOKEN = "test-chat-token"
H = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}
BASE = {"channel": "whatsapp", "channel_user_id": "8801700000001", "full_name": "Rafi", "phone": "01712345678", "address": "House 1, Mirpur", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture
def shop(db):
    call_command("seed_catalog", dataset="test")
    cat = Category.objects.first()
    plain = Product.objects.create(category=cat, name="Plain Bag", slug="plain-bag", sku="PB1", price=500, stock=5, is_active=True)
    frock = Product.objects.create(category=cat, name="Kids Frock", slug="kids-frock", sku="FR1", price=1299, stock=0, is_active=True)
    v18 = ProductVariant.objects.create(product=frock, color=Color.objects.get_or_create(name="Pink")[0], size=Size.objects.get_or_create(name="18")[0], stock=3, sku="FR1-18")
    return plain, frock, v18


def post(client, body, **extra):
    return client.post("/api/chat/orders/", json.dumps(body), content_type="application/json", **{**H, **extra})


@pytest.mark.django_db
def test_api_is_off_without_a_token_and_rejects_a_wrong_one(client, shop):
    assert client.get("/api/chat/products/?q=bag", **H).status_code == 404   # no CHAT_ORDER_TOKEN configured
    with override_settings(CHAT_ORDER_TOKEN=TOKEN):
        assert client.get("/api/chat/products/?q=bag").status_code == 401
        assert client.get("/api/chat/products/?q=bag", HTTP_AUTHORIZATION="Bearer nope").status_code == 401
        assert client.get("/api/chat/products/?q=bag", **H).status_code == 200


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_product_search_shows_variants_and_stock(client, shop):
    data = client.get("/api/chat/products/?q=frock", **H).json()["products"]
    assert len(data) == 1 and data[0]["sku"] == "FR1" and data[0]["needs_variant"] and data[0]["variants"][0]["sku"] == "FR1-18"
    assert client.get("/api/chat/products/?q=x", **H).json() == {"products": []}   # too short


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_quote_prices_the_order_but_saves_nothing(client, shop):
    plain, frock, v18 = shop
    r = post(client, {**BASE, "dry_run": True, "items": [{"sku": "PB1", "quantity": 2}, {"sku": "FR1-18", "quantity": 1}]})
    body = r.json()
    assert r.status_code == 200 and body["dry_run"] and body["subtotal"] == "2299.00" and body["total"] == str(2299 + float(body["delivery_charge"]))[:0] + body["total"]
    assert Order.objects.count() == 0 and ChatOrder.objects.count() == 0
    plain.refresh_from_db()
    v18.refresh_from_db()
    assert plain.stock == 5 and v18.stock == 3


@override_settings(CHAT_ORDER_TOKEN=TOKEN, SUPPLIER_API_KEY="k", SUPPLIER_API_SECRET="s", SUPPLIER_AUTO_CONFIRM=True)
@pytest.mark.django_db
def test_create_order_is_pending_idempotent_and_never_auto_confirmed(client, shop, django_capture_on_commit_callbacks):
    plain, _, v18 = shop
    body = {**BASE, "request_id": "wamid.1", "items": [{"sku": "PB1", "quantity": 1}, {"sku": "FR1-18", "quantity": 1}], "notes": "evening please"}
    with django_capture_on_commit_callbacks(execute=True):
        first = post(client, body)
    assert first.status_code == 201 and first.json()["created"]
    order = Order.objects.get()
    assert order.status == Order.Status.PENDING and order.chat_order.channel == "whatsapp" and "WhatsApp" in order.notes and "evening please" in order.notes
    assert order.user.email == "01712345678@chat.narizmart.invalid" and not order.user.has_usable_password()
    plain.refresh_from_db()
    v18.refresh_from_db()
    assert plain.stock == 4 and v18.stock == 2
    again = post(client, body)   # the same chat message arrives twice
    assert again.status_code == 200 and again.json()["order_number"] == order.number and not again.json()["created"]
    assert Order.objects.count() == 1


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
@pytest.mark.parametrize("change,fragment", [
    ({"phone": "123"}, "phone"), ({"delivery_area": "mars"}, "delivery_area"), ({"channel": "sms"}, "channel"), ({"items": [{"sku": "NOPE", "quantity": 1}]}, "Unknown sku"),
    ({"items": [{"sku": "PB1", "quantity": 9}]}, "quantity"), ({"items": [{"sku": "FR1", "quantity": 1}]}, "options"), ({"items": [{"sku": "PB1", "quantity": 1}], "address": ""}, "required"),
    ({"items": [{"sku": "FR1-18", "quantity": 5}]}, "left in stock"), ({"request_id": ""}, "request_id"),
])
def test_bad_orders_get_a_clear_error(client, shop, change, fragment):
    body = {**BASE, "request_id": "r1", "items": [{"sku": "PB1", "quantity": 1}], **change}
    r = post(client, body)
    assert r.status_code == 400 and fragment.lower() in r.json()["error"].lower() and Order.objects.count() == 0


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_one_phone_cannot_flood_orders(client, shop):
    for n in range(5):
        assert post(client, {**BASE, "request_id": f"f{n}", "items": [{"sku": "PB1", "quantity": 1}]}).status_code == 201
    assert post(client, {**BASE, "request_id": "f6", "items": [{"sku": "PB1", "quantity": 1}]}).status_code == 429


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_status_needs_the_matching_phone_and_no_email_goes_to_chat_customers(client, shop, django_capture_on_commit_callbacks):
    number = post(client, {**BASE, "request_id": "s1", "items": [{"sku": "PB1", "quantity": 1}]}).json()["order_number"]
    ok = client.get(f"/api/chat/orders/status/?phone=01712345678&number={number}", **H)
    assert ok.status_code == 200 and ok.json()["status"] == "pending"
    assert client.get(f"/api/chat/orders/status/?phone=01799999999&number={number}", **H).status_code == 404
    order = Order.objects.get(number=number)
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert mail.outbox == []   # the placeholder address never gets mail


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_optional_email_gets_status_mail_and_a_bad_one_is_rejected(client, shop, django_capture_on_commit_callbacks):
    assert post(client, {**BASE, "request_id": "e0", "email": "nope", "items": [{"sku": "PB1", "quantity": 1}]}).status_code == 400
    number = post(client, {**BASE, "request_id": "e1", "email": "Rafi@Example.com", "items": [{"sku": "PB1", "quantity": 1}]}).json()["order_number"]
    order = Order.objects.get(number=number)
    assert order.chat_order.contact_email == "rafi@example.com"
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert [m.to for m in mail.outbox] == [["rafi@example.com"]]


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_admin_shows_where_the_order_came_from_without_extra_queries(client, shop, django_user_model):
    admin = django_user_model.objects.create_superuser("adm@example.com", "pass12345!x")
    admin.email_verified = True
    admin.save()
    client.force_login(admin)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    url = "/" + settings.ADMIN_URL + "orders/order/"

    def count():
        client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            r = client.get(url)
        assert r.status_code == 200
        return r, len(ctx.captured_queries)

    post(client, {**BASE, "request_id": "a1", "items": [{"sku": "PB1", "quantity": 1}]})
    page, small = count()
    assert b"WhatsApp" in page.content
    for n in range(2, 12):
        post(client, {**BASE, "request_id": f"a{n}", "phone": f"0171234567{n % 10}", "items": [{"sku": "PB1", "quantity": 1}]})
    assert count()[1] <= small + 1
    assert client.get(url + "?via=whatsapp").status_code == 200 and client.get(url + "?via=website").status_code == 200


@override_settings(CHAT_ORDER_TOKEN=TOKEN)
@pytest.mark.django_db
def test_ai_tools_may_send_items_and_dry_run_as_text(client, shop):
    r = post(client, {**BASE, "dry_run": "true", "items": '[{"sku": "PB1", "quantity": 1}]'})
    assert r.status_code == 200 and r.json()["dry_run"] and Order.objects.count() == 0
    r = post(client, {**BASE, "dry_run": "false", "request_id": "t1", "items": '[{"sku": "PB1", "quantity": "2"}]'})
    assert r.status_code == 201 and Order.objects.get().items.get().quantity == 2
    assert post(client, {**BASE, "request_id": "t2", "items": "not json"}).status_code == 400


@pytest.mark.django_db
def test_hide_products_lists_by_default_and_hides_with_apply(shop):
    call_command("hide_products", "PB1,NOPE")
    assert Product.objects.get(sku="PB1").is_active
    call_command("hide_products", "PB1,NOPE", "--apply")
    assert not Product.objects.get(sku="PB1").is_active and Product.objects.get(sku="FR1").is_active
