"""Checkout races. These need real row locks, so they only run on PostgreSQL (CI runs them; SQLite skips)."""
import threading

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections

from apps.catalog.models import Product
from apps.orders.models import Order
from apps.orders.services import OrderError, create_order

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="needs PostgreSQL row locking"),
]
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


def test_two_buyers_cannot_take_the_last_item():
    call_command("seed_catalog", dataset="test")
    product = Product.objects.get(slug="retro-everyday-sunglasses")
    Product.objects.filter(pk=product.pk).update(stock=1)
    User = get_user_model()
    users = [User.objects.create_user(f"u{i}@example.com", "pass12345!x", email_verified=True) for i in range(2)]
    barrier = threading.Barrier(2)
    results = []

    def buy(user):
        try:
            barrier.wait(timeout=10)
            create_order(user=user, lines=[(product.pk, 0, 1)], data=DELIVERY)
            results.append("ok")
        except OrderError:
            results.append("sold out")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=buy, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    product.refresh_from_db()
    assert sorted(results) == ["ok", "sold out"]
    assert Order.objects.count() == 1 and product.stock == 0
