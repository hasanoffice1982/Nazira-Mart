import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.catalog.models import Product
from apps.orders.models import Order
from apps.orders.services import change_status, create_order
from apps.supplier import client, importer, services
from apps.supplier.models import SupplierOrder, SupplierProduct

pytestmark = pytest.mark.django_db
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1, Mirpur 1216", "city": "Dhaka", "delivery_area": "dhaka"}
KEYS = {"SUPPLIER_API_KEY": "k", "SUPPLIER_API_SECRET": "s"}


@pytest.fixture
def order(django_user_model):
    call_command("seed_catalog", dataset="test")
    user = django_user_model.objects.create_user("u@example.com", "pass12345!x", email_verified=True)
    product = Product.objects.get(slug="retro-everyday-sunglasses")
    SupplierProduct.objects.create(product=product, supplier_product_id="VND-105")
    return create_order(user=user, lines=[(product.pk, 0, 1)], data=DELIVERY)


@pytest.fixture
def calls(monkeypatch):
    sent = []

    def fake(method, path, payload=None):
        sent.append((method, path, payload))
        return {"success": True, "order_details": {"system_order_id": "API-1", "status": "New"}}

    monkeypatch.setattr(client, "call", fake)
    return sent


@override_settings(**KEYS)
def test_send_order_uses_supplier_product_id_zone_postal_and_no_price(order, calls):
    parcels = services.send_order(order)
    assert [p.state for p in parcels] == ["sent"] and parcels[0].supplier_order_id == "API-1"
    payload = calls[0][2]
    assert payload["product_id"] == "VND-105" and payload["city_or_zone"] == "Inside Dhaka" and payload["postal_code"] == "1216"
    assert payload["payment_method"] == "Cash on Delivery" and payload["partner_order_id"] == order.number
    assert not {"price", "amount", "total"} & payload.keys()


@override_settings(**KEYS)
def test_sending_twice_does_not_create_a_second_parcel(order, calls):
    services.send_order(order)
    services.send_order(order)
    assert len(calls) == 1 and SupplierOrder.objects.count() == 1


@override_settings(**KEYS)
def test_failure_is_recorded_and_can_be_retried(order, monkeypatch):
    def boom(*a, **k):
        raise client.SupplierError("HTTP 403: IP blocked")

    monkeypatch.setattr(client, "call", boom)
    (parcel,) = services.send_order(order)
    assert parcel.state == "failed" and "IP blocked" in parcel.error
    monkeypatch.setattr(client, "call", lambda *a, **k: {"order_details": {"system_order_id": "API-9", "status": "New"}})
    (parcel,) = services.send_order(order)
    assert parcel.state == "sent" and parcel.error == ""


@override_settings(**KEYS)
def test_quantity_over_one_needs_a_manual_order(django_user_model, calls):
    call_command("seed_catalog", dataset="test")
    product = Product.objects.get(slug="retro-everyday-sunglasses")
    SupplierProduct.objects.create(product=product, supplier_product_id="VND-1")
    user = django_user_model.objects.create_user("q@example.com", "pass12345!x", email_verified=True)
    o = create_order(user=user, lines=[(product.pk, 0, 2)], data=DELIVERY)
    (parcel,) = services.send_order(o)
    assert parcel.state == "manual" and not calls


@override_settings(SUPPLIER_AUTO_SEND=True, **KEYS)
def test_confirming_an_order_sends_it_automatically(order, calls, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert len(calls) == 1


@override_settings(SUPPLIER_AUTO_SEND=False, **KEYS)
def test_auto_send_is_off_by_default(order, calls, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert calls == []


def test_without_keys_nothing_is_sent(order, calls, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert calls == []


@override_settings(SUPPLIER_WEBHOOK_TOKEN="tok123", **KEYS)
def test_webhook_flow_shipped_then_delivered_then_wrong_token(order, calls, client):
    change_status(order, Order.Status.CONFIRMED)
    services.send_order(order)
    url = "/supplier/webhook/tok123/"
    body = {"event": "order.status_updated", "partner_order_id": order.number, "new_status": "Shipped", "courier_name": "Steadfast",
            "tracking_code": "ST-1", "tracking_link": "https://steadfast.com.bd/track/ST-1"}
    r = client.post(url, json.dumps(body), content_type="application/json")
    order.refresh_from_db()
    assert r.status_code == 200 and order.status == "shipped" and order.tracking_number == "ST-1"
    client.post(url, json.dumps({**body, "new_status": "Delivered"}), content_type="application/json")
    order.refresh_from_db()
    assert order.status == "delivered" and order.payment_status == "paid"
    assert client.post("/supplier/webhook/wrong/", json.dumps(body), content_type="application/json").status_code == 404


@override_settings(SUPPLIER_WEBHOOK_TOKEN="tok123", **KEYS)
def test_webhook_refused_parcel_becomes_returned(order, calls, client):
    change_status(order, Order.Status.CONFIRMED)
    services.send_order(order)
    body = {"event": "order.status_updated", "partner_order_id": order.number}
    for st in ("Shipped", "Returned"):
        client.post("/supplier/webhook/tok123/", json.dumps({**body, "new_status": st}), content_type="application/json")
    order.refresh_from_db()
    assert order.status == "returned"


def test_webhook_is_off_without_a_token(client):
    assert client.post("/supplier/webhook//", "{}", content_type="application/json").status_code == 404
    assert client.get("/supplier/webhook/x/").status_code == 405


ITEMS = [
    {"product_id": "VND-1", "title": "Elegant Pastel Pink Pearl Statement Earrings", "description": "<p>Nice</p>", "thumbnail": "", "images": "[]",
     "base_price": 150, "partner_commission": 95, "final_selling_price": 245, "stock": 20, "is_active": 1},
    {"product_id": "VND-2", "title": "Cute Hello Kitty Printed Pink Girls Two-Piece Set", "final_selling_price": 389, "stock": 9, "is_active": 1},
    {"product_id": "VND-3", "title": "Rolex Quartz Movement Stainless Steel Analog Watch for Men", "final_selling_price": 722, "stock": 9, "is_active": 1},
    {"product_id": "VND-4", "title": "Smart toy car sold out", "final_selling_price": 433, "stock": 0, "is_active": 1},
]


def test_import_creates_hidden_products_in_guessed_categories_and_skips_the_rest():
    call_command("seed_catalog")
    report = importer.sync(items=ITEMS, photos=False)
    assert (report.created, report.skipped) == (2, 2)
    earrings = Product.objects.get(sku="VND-1")
    assert earrings.is_active is False and earrings.price == Decimal("245.00") and earrings.category.slug == "jewellery"
    assert Product.objects.get(sku="VND-2").category.slug == "kids"


def test_import_twice_only_refreshes_price_and_stock_and_keeps_edits():
    call_command("seed_catalog")
    importer.sync(items=ITEMS, photos=False)
    Product.objects.filter(sku="VND-1").update(name="My own name", is_active=True)
    changed = [{**ITEMS[0], "final_selling_price": 299, "stock": 5}]
    report = importer.sync(items=changed, photos=False)
    p = Product.objects.get(sku="VND-1")
    assert report.updated == 1 and report.created == 0
    assert (p.price, p.stock, p.name, p.is_active) == (Decimal("299.00"), 5, "My own name", True)


def test_dry_run_changes_nothing():
    call_command("seed_catalog")
    before = Product.objects.count()
    report = importer.sync(items=ITEMS, photos=False, dry_run=True)
    assert report.created == 2 and Product.objects.count() == before


def test_image_urls_resolve_relative_names_next_to_the_thumbnail():
    urls = importer.image_urls({"thumbnail": "https://x.test/uploads/t.jpg", "images": '["a.jpg","b.jpg"]'})
    assert urls == ["https://x.test/uploads/t.jpg", "https://x.test/uploads/a.jpg", "https://x.test/uploads/b.jpg"]


# --- automation ---------------------------------------------------------------------
@override_settings(SUPPLIER_AUTO_CONFIRM=True, SUPPLIER_AUTO_SEND=True, **KEYS)
def test_safe_new_order_is_confirmed_and_sent_without_a_call(order, calls, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        assert services.maybe_auto_confirm(order.pk) is True
    order.refresh_from_db()
    assert order.status == "confirmed" and len(calls) == 1


@override_settings(SUPPLIER_AUTO_CONFIRM=True, **KEYS)
def test_phone_that_refused_before_needs_a_call(order, calls, django_user_model):
    other = django_user_model.objects.create_user("o@example.com", "pass12345!x", email_verified=True)
    old = create_order(user=other, lines=[(Product.objects.get(slug="retro-everyday-sunglasses").pk, 0, 1)], data=DELIVERY)
    for st in (Order.Status.CONFIRMED, Order.Status.PROCESSING, Order.Status.SHIPPED, Order.Status.RETURNED):
        change_status(old, st)
    assert services.maybe_auto_confirm(order.pk) is False


@override_settings(SUPPLIER_AUTO_CONFIRM=True, SUPPLIER_AUTO_CONFIRM_MAX_TOTAL=100, **KEYS)
def test_big_orders_are_not_auto_confirmed(order, calls):
    assert services.maybe_auto_confirm(order.pk) is False


@override_settings(SUPPLIER_AUTO_CONFIRM=False, **KEYS)
def test_auto_confirm_is_off_by_default(order, calls):
    assert services.maybe_auto_confirm(order.pk) is False


@override_settings(SUPPLIER_AUTO_SEND=True, **KEYS)
def test_cancelling_an_order_cancels_it_at_the_supplier(order, calls):
    services.send_order(order)
    calls.clear()
    services.cancel_at_supplier(order)
    assert calls and calls[0][2] == {"status": "Canceled"} and "API-1" in calls[0][1]


@override_settings(**KEYS)
def test_retry_resends_failed_parcels(order, monkeypatch):
    change_status(order, Order.Status.CONFIRMED)
    monkeypatch.setattr(client, "call", lambda *a, **k: (_ for _ in ()).throw(client.SupplierError("down")))
    services.send_order(order)
    monkeypatch.setattr(client, "call", lambda *a, **k: {"order_details": {"system_order_id": "API-5", "status": "New"}})
    assert services.retry_failed() == 1


@override_settings(SUPPLIER_SYNC_INCLUDE="", **KEYS)
def test_cron_without_an_include_list_only_refreshes(monkeypatch, capsys):
    call_command("seed_catalog")
    monkeypatch.setattr(client, "call", lambda m, p, payload=None: {"data": ITEMS} if p == "/products" else {"data": {"current_balance": 9000, "minimum_required": 200, "currency": "BDT"}})
    before = Product.objects.count()
    call_command("supplier_cron")
    assert Product.objects.count() == before


@override_settings(SUPPLIER_SYNC_INCLUDE="earring|girls", SUPPLIER_SYNC_ACTIVATE=True, **KEYS)
def test_cron_with_an_include_list_imports_and_activates(monkeypatch):
    call_command("seed_catalog")
    monkeypatch.setattr(importer, "_add_photos", lambda *a, **k: None)
    monkeypatch.setattr(client, "call", lambda m, p, payload=None: {"data": ITEMS} if p == "/products" else {"data": {"current_balance": 9000, "minimum_required": 200}})
    call_command("supplier_cron")
    assert Product.objects.filter(sku__in=["VND-1", "VND-2"], is_active=True).count() == 2


@override_settings(SUPPLIER_LOW_BALANCE=500, **KEYS)
def test_low_balance_sends_one_alert_email(monkeypatch, mailoutbox):
    from django.core.cache import cache

    cache.delete("supplier_low_balance_mail")
    monkeypatch.setattr(client, "call", lambda m, p, payload=None: {"data": {"current_balance": 120, "minimum_required": 200}} if p == "/balance" else {"data": []})
    call_command("supplier_cron")
    call_command("supplier_cron")
    assert len(mailoutbox) == 1 and "wallet is low" in mailoutbox[0].subject


class TestVariants:
    def test_parses_bangla_size_list(self):
        from apps.supplier.variants import parse_colors, parse_sizes
        text = "সাইজ ও বয়স (Size & Age Guide):সাইজ (Size): ১৬, ১৮, ২০, ২২বয়স (Age Group): ১ থেকে ১০ বছর। Color: Pink, Sky Blue"
        assert parse_sizes(text) == ["16", "18", "20", "22"]
        assert parse_colors(text) == ["Pink", "Sky Blue"]
        assert parse_sizes("Soft cotton hoodie") == [] and parse_colors("Soft cotton hoodie") == []

    @pytest.mark.django_db
    def test_import_creates_variants_and_variant_order_goes_manual(self, settings):
        from apps.catalog.models import Product
        from apps.supplier import importer
        item = {"product_id": "9001", "title": "Kids frock", "description": "Size: 16, 18\nColor: Pink, Blue", "final_selling_price": 666, "base_price": 500, "stock": 5, "is_active": 1}
        importer.sync(items=[item], photos=False, only_known_category=False, activate=True)
        product = Product.objects.get(sku="9001")
        assert product.variants.count() == 4
        importer.sync(items=[{**item, "stock": 2}], photos=False)
        assert set(product.variants.values_list("stock", flat=True)) == {2}


@override_settings(**KEYS)
def test_variant_item_goes_manual_by_default_and_into_address_in_note_mode(order, calls):
    item = order.items.get()
    item.variant_label = "Pink / 18"
    item.save()
    parcel = services.send_order(order)[0]
    assert parcel.state == "manual" and "Pink / 18" in parcel.error and not calls
    parcel.delete()
    with override_settings(SUPPLIER_VARIANT_MODE="address_note"):
        assert services.send_order(order)[0].state == "sent"
    assert calls[0][2]["customer_address"].endswith("[Pink / 18]")
