import pytest
from django.core.management import call_command

from apps.catalog.models import Product
from apps.orders.models import Order
from apps.orders.services import OrderError, change_status, create_order

pytestmark = pytest.mark.django_db
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture
def order(django_user_model):
    call_command("seed_catalog", dataset="test")
    user = django_user_model.objects.create_user("u@example.com", "pass12345!x", email_verified=True)
    product = Product.objects.get(slug="retro-everyday-sunglasses")
    start = product.stock
    o = create_order(user=user, lines=[(product.pk, 0, 2)], data=DELIVERY)
    return o, product, start


def to_shipped(o):
    for s in (Order.Status.CONFIRMED, Order.Status.PROCESSING, Order.Status.SHIPPED):
        change_status(o, s)


def test_refused_parcel_is_returned_and_stock_goes_back(order):
    o, product, start = order
    to_shipped(o)
    change_status(o, Order.Status.RETURNED, note="refused at door")
    o.refresh_from_db(); product.refresh_from_db()
    assert o.status == "returned" and o.payment_status == "unpaid" and product.stock == start


def test_delivered_then_returned_is_marked_refunded(order):
    o, product, start = order
    to_shipped(o)
    change_status(o, Order.Status.DELIVERED)
    change_status(o, Order.Status.RETURNED)
    o.refresh_from_db()
    assert o.payment_status == "refunded"


def test_returned_is_final_and_only_after_dispatch(order):
    o, _, _ = order
    with pytest.raises(OrderError):
        change_status(o, Order.Status.RETURNED)   # still pending: cancel it instead
    to_shipped(o)
    change_status(o, Order.Status.RETURNED)
    with pytest.raises(OrderError):
        change_status(o, Order.Status.DELIVERED)


def test_returned_orders_do_not_count_as_best_sellers(order):
    from apps.catalog.popular import best_seller_ids

    o, product, _ = order
    to_shipped(o)
    change_status(o, Order.Status.DELIVERED)
    assert product.pk in best_seller_ids(0, 5)
    change_status(o, Order.Status.RETURNED)
    assert product.pk not in best_seller_ids(0, 5)
