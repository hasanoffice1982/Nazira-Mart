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
    assert payload["reference"] == f"{order.number}-{dealer.pk}" and payload["cod_amount"] == str(order.total)
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
def test_csv_dealer_column(settings):
    Category.objects.create(name="Kids", slug="kids", title="Kids")
    csv_text = "name,slug,sku,category,price,stock,dealer\nFrock,frock,D1,Kids,999,5,Rahim Traders\n"
    plan = importer.parse(csv_text.encode())
    assert plan.errors and "doesn't exist" in plan.errors[0][1]
    Dealer.objects.create(name="Rahim Traders")
    plan = importer.parse(csv_text.encode())
    assert not plan.errors
    importer.apply(plan)
    assert Product.objects.get(slug="frock").dealer.name == "Rahim Traders"


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
