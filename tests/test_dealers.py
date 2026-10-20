import hashlib
import hmac
import json
import urllib.error

import pytest
from django.core.management import call_command

from apps.catalog import importer
from apps.catalog.models import Category, Product
from apps.dealers import services
from apps.dealers.models import Dealer, DealerOrder
from apps.orders.models import Order
from apps.orders.services import change_status, create_order
from apps.supplier.models import SupplierProduct
from tests.test_supplier import DELIVERY

HOOK = "https://n8n.example.com/webhook/rahim"


@pytest.fixture
def setup(django_user_model):
    call_command("seed_catalog", dataset="test")
    user = django_user_model.objects.create_user("u@example.com", "pass12345!x", email_verified=True)
    dealer = Dealer.objects.create(name="Rahim Traders", webhook_url=HOOK)
    product = Product.objects.get(slug="retro-everyday-sunglasses")
    product.dealer = dealer
    product.save()
    order = create_order(user=user, lines=[(product.pk, 0, 1)], data=DELIVERY)
    return dealer, product, order


@pytest.fixture
def posts(monkeypatch):
    sent = []
    monkeypatch.setattr(services, "_post", lambda dealer, payload: sent.append((dealer.name, payload)))
    return sent


@pytest.mark.django_db
def test_fulfilment_kind_of_a_product(setup):
    dealer, product, _ = setup
    assert product.fulfilment == ("dealer", "Dealer: Rahim Traders")
    product.dealer = None
    assert product.fulfilment == ("own", "Own stock")
    SupplierProduct.objects.create(product=product, supplier_product_id="VND-1")
    product.refresh_from_db()
    assert product.fulfilment == ("dropship", "Dropship")


@pytest.mark.django_db
def test_confirming_sends_the_dealers_items_with_cod_and_reference(setup, posts, django_capture_on_commit_callbacks):
    dealer, product, order = setup
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    assert len(posts) == 1
    name, payload = posts[0]
    assert name == "Rahim Traders" and payload["event"] == "order.confirmed"
    assert payload["dealer"] == {"id": dealer.pk, "name": "Rahim Traders", "phone": "", "email": ""}
    assert payload["reference"] == f"{order.number}-{dealer.pk}" and payload["cod_amount"] == str(order.total) and payload["delivery_prepaid"] is False
    assert payload["items"][0]["sku"] == product.sku and payload["customer"]["phone"] == "01712345678"
    assert payload["callback_url"].endswith(f"/dealer/webhook/{dealer.token}/")
    assert DealerOrder.objects.get(order=order).state == "sent"
    with django_capture_on_commit_callbacks(execute=True):   # confirming again must not send twice
        services.send_order(order)
    assert len(posts) == 1


@pytest.mark.django_db
def test_signature_is_hmac_of_the_body(setup, monkeypatch):
    dealer, _, order = setup
    seen = {}

    class Resp:
        def read(self, n): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_open(req, timeout=0):
        seen["body"], seen["sig"] = req.data, req.headers["X-narizmart-signature"]
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    services._post(dealer, {"hello": "world"})
    assert seen["sig"] == "sha256=" + hmac.new(dealer.secret.encode(), seen["body"], hashlib.sha256).hexdigest()
    assert json.loads(seen["body"]) == {"hello": "world"}


@pytest.mark.django_db
def test_dealer_without_webhook_is_handled_by_hand_and_failures_are_retried(setup, monkeypatch):
    dealer, _, order = setup
    dealer.webhook_url = ""
    dealer.save()
    assert services.send_order(order)[0].state == "manual"
    dealer.webhook_url = HOOK
    dealer.save()
    monkeypatch.setattr(services, "_post", lambda d, p: (_ for _ in ()).throw(urllib.error.URLError("down")))
    assert services.send_order(order)[0].state == "failed"
    sent = []
    monkeypatch.setattr(services, "_post", lambda d, p: sent.append(p))
    assert services.send_order(order)[0].state == "sent" and len(sent) == 1


@pytest.mark.django_db
def test_tracking_webhook_updates_order_and_rejects_wrong_token(setup, posts, client, django_capture_on_commit_callbacks):
    dealer, _, order = setup
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, Order.Status.CONFIRMED)
    url = f"/dealer/webhook/{dealer.token}/"
    body = {"reference": f"{order.number}-{dealer.pk}", "status": "shipped", "courier_name": "Pathao", "tracking_code": "PT123", "tracking_link": "https://t.example/PT123"}
    assert client.post(url, json.dumps(body), content_type="application/json").json() == {"ok": True}
    order.refresh_from_db()
    assert order.status == Order.Status.SHIPPED and order.tracking_number == "PT123"
    part = DealerOrder.objects.get(order=order)
    assert part.courier_name == "Pathao" and part.dealer_status == "shipped"
    client.post(url, json.dumps({"order_number": order.number, "status": "delivered"}), content_type="application/json")   # order number works when there is one dealer part
    order.refresh_from_db()
    assert order.status == Order.Status.DELIVERED
    assert client.post("/dealer/webhook/wrong/", "{}", content_type="application/json").status_code == 404
    assert client.get(url).status_code == 405


@pytest.mark.django_db
def test_csv_ships_from_column(settings):
    Category.objects.create(name="Kids", slug="kids", title="Kids")
    head = "name,slug,sku,category,price,stock,ships_from,supplier_id\n"
    # required for a new product
    assert importer.parse((head + "Frock,frock,D1,Kids,999,5,,\n").encode()).errors
    # an unknown dealer is refused
    plan = importer.parse((head + "Frock,frock,D1,Kids,999,5,Rahim Traders,\n").encode())
    assert plan.errors and "Dealers" in plan.errors[0][1]
    Dealer.objects.create(name="Rahim Traders")
    plan = importer.parse((head + "Frock,frock,D1,Kids,999,5,Rahim Traders,\n" + "Hoodie,hoodie,325,Kids,849,5,dropship,\n" + "Bag,bag,B1,Kids,500,5,own,\n").encode())
    assert not plan.errors
    importer.apply(plan)
    assert Product.objects.get(slug="frock").fulfilment == ("dealer", "Dealer: Rahim Traders")
    assert Product.objects.get(slug="hoodie").supplier_link.supplier_product_id == "325"   # supplier id defaults to the sku
    assert Product.objects.get(slug="bag").fulfilment == ("own", "Own stock")
    # changing a row to another source updates it; the same supplier id on two products is refused
    assert importer.parse((head + "Hoodie,hoodie,325,Kids,849,5,own,\n" + "Bag,bag,B1,Kids,500,5,dropship,325\n").encode()).errors   # one id cannot move between products inside one file
    importer.apply(importer.parse((head + "Hoodie,hoodie,325,Kids,849,5,own,\n").encode()))
    assert Product.objects.get(slug="hoodie").fulfilment[0] == "own"
    importer.apply(importer.parse((head + "Bag,bag,B1,Kids,500,5,dropship,325\n").encode()))
    assert Product.objects.get(slug="bag").supplier_link.supplier_product_id == "325"
    clash = importer.parse((head + "Hoodie,hoodie,325,Kids,849,5,dropship,\n").encode())
    assert clash.errors and "already used" in clash.errors[0][1]


@pytest.mark.django_db
def test_admin_lists_show_who_ships_and_filter(setup, client, django_user_model):
    dealer, product, order = setup
    admin = django_user_model.objects.create_superuser("adm@example.com", "pass12345!x")
    admin.email_verified = True
    admin.save()
    client.force_login(admin)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    from django.conf import settings
    base = "/" + settings.ADMIN_URL
    products = client.get(base + "catalog/product/")
    assert products.status_code == 200 and b"Dealer: Rahim Traders" in products.content
    assert client.get(base + f"catalog/product/?ships=dealer-{dealer.pk}").status_code == 200
    orders = client.get(base + "orders/order/")
    assert orders.status_code == 200 and b"Dealer: Rahim Traders" in orders.content
    assert client.get(base + f"orders/order/?ships=dealer-{dealer.pk}").status_code == 200
    assert client.get(base + "orders/order/?ships=dropship").status_code == 200
    assert client.get(base + "dealers/dealer/").status_code == 200
    assert client.get(base + f"dealers/dealer/{dealer.pk}/change/").status_code == 200
    assert client.get(base + f"orders/order/{order.pk}/change/").status_code == 200


@pytest.mark.django_db
def test_link_dropship_command_and_supplier_import_corrects_the_id(settings):
    from apps.supplier import importer as supplier_importer

    cat = Category.objects.create(name="Kids", slug="kids", title="Kids")
    a = Product.objects.create(category=cat, name="Frock", slug="frock", sku="8971", price=1299, stock=0)
    Product.objects.create(category=cat, name="Own thing", slug="own", sku="NM-1", price=500, stock=3)
    call_command("link_dropship", "--exclude", "999")
    a.refresh_from_db()
    assert a.supplier_link.supplier_product_id == "8971" and a.fulfilment[0] == "dropship"
    assert Product.objects.get(slug="own").fulfilment[0] == "own"   # non-numeric sku is left alone
    item = {"product_id": "VND-8971", "system_id": 8971, "title": "Kids Frock", "final_selling_price": 1299, "base_price": 666, "stock": 4, "is_active": 1}
    supplier_importer.sync(items=[item], photos=False)
    a.refresh_from_db()
    assert Product.objects.count() == 2 and a.supplier_link.supplier_product_id == "VND-8971" and a.stock == 4


@pytest.mark.django_db
def test_prepaid_delivery_charge_is_not_collected_by_the_courier(setup):
    dealer, _, order = setup
    order.delivery_prepaid = True
    payload = services.build_payload(order, dealer, list(order.items.all()), "ref")
    assert payload["delivery_prepaid"] is True and payload["cod_amount"] == str(order.total - order.delivery_charge)
