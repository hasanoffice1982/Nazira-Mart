"""Guard rails against slow pages: the number of database queries must not grow with the number of products or rows shown.
If one of these fails, a page started querying per product/row (the classic N+1): add select_related/prefetch_related."""
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.catalog.models import Category, Color, Product, ProductVariant, Size
from apps.dealers.models import Dealer, DealerOrder
from apps.orders.services import create_order
from apps.supplier.models import SupplierProduct

DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1, Mirpur 1216", "city": "Dhaka", "delivery_area": "dhaka"}


def make_products(start: int, n: int, dealer=None):
    cats = list(Category.objects.all())
    color = Color.objects.get_or_create(name="Red")[0]
    size = Size.objects.get_or_create(name="M")[0]
    out = []
    for i in range(start, start + n):
        p = Product.objects.create(category=cats[i % len(cats)], name=f"QC {i}", slug=f"qc-{i}", sku=f"QC{i}", price=500 + i, stock=5, is_active=True, dealer=dealer if i % 2 else None)
        ProductVariant.objects.create(product=p, color=color, size=size, stock=4)
        if i % 3 == 0:
            SupplierProduct.objects.create(product=p, supplier_product_id=f"S{i}")
        out.append(p)
    return out


def queries(client, url, **kw) -> int:
    client.get(url, **kw)   # warm the small caches (categories, footer pages…) like a second visitor
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url, follow=True, **kw)
    assert resp.status_code == 200, url
    return len(ctx.captured_queries)


@pytest.fixture
def shop(django_user_model):
    call_command("seed_catalog")
    make_products(0, 12)
    user = django_user_model.objects.create_user("u@example.com", "pass12345!x", email_verified=True)
    return user


@pytest.mark.django_db
@pytest.mark.parametrize("url,cap", [("/", 10), ("/shop/", 6), ("/shop/?category=women", 8), ("/shop/?q=qc", 6), ("/shop/fragment/?page=1", 6), ("/search/suggest/?q=qc", 5)])
def test_storefront_pages_stay_within_budget_and_do_not_grow(client, shop, url, cap):
    small = queries(client, url)
    make_products(100, 30)   # many more products: the count must not change
    assert queries(client, url) <= small
    assert small <= cap, f"{url} runs {small} queries (budget {cap})"


@pytest.mark.django_db
def test_product_pages_do_not_query_per_variant_or_photo(client, shop):
    p = Product.objects.get(slug="qc-1")
    for n in range(6):   # many variants must not add queries
        ProductVariant.objects.create(product=p, color=Color.objects.get_or_create(name=f"C{n}")[0], size=Size.objects.get_or_create(name=f"S{n}")[0], stock=1)
    assert queries(client, f"/product/{p.slug}/") <= 5
    assert queries(client, f"/product/{p.slug}/quick/") <= 5


@pytest.mark.django_db
def test_cart_pages_load_the_cart_once(client, shop):
    user = shop
    products = Product.objects.filter(slug__startswith="qc-")[:5]
    client.force_login(user)
    for p in products:
        client.post(f"/cart/add/{p.pk}/", {"quantity": 1, "variant": p.variants.first().pk})
    checkout = queries(client, "/orders/checkout/")
    assert checkout <= 14, f"checkout runs {checkout} queries"
    for p in Product.objects.filter(slug__startswith="qc-")[5:10]:   # more cart lines: no extra queries
        client.post(f"/cart/add/{p.pk}/", {"quantity": 1, "variant": p.variants.first().pk})
    assert queries(client, "/orders/checkout/") <= checkout
    assert queries(client, "/") <= 16


@pytest.mark.django_db
def test_account_pages_are_flat(client, shop):
    user = shop
    products = Product.objects.filter(slug__startswith="qc-")[:3]
    for p in products:
        create_order(user=user, lines=[(p.pk, p.variants.first().pk, 1)], data=DELIVERY)
    client.force_login(user)
    first = queries(client, "/orders/")
    for p in Product.objects.filter(slug__startswith="qc-")[3:9]:
        create_order(user=user, lines=[(p.pk, p.variants.first().pk, 1)], data=DELIVERY)
    assert queries(client, "/orders/") <= first
    assert first <= 10


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["catalog/product/", "catalog/product/?ships=dropship", "orders/order/", "dealers/dealer/", "dealers/dealerorder/", "supplier/supplierorder/", "supplier/supplierproduct/", "catalog/category/"])
def test_admin_lists_do_not_query_per_row(client, django_user_model, shop, url):
    admin = django_user_model.objects.create_superuser("adm@example.com", "pass12345!x")
    admin.email_verified = True
    admin.save()
    client.force_login(admin)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    dealer = Dealer.objects.create(name="D1")

    def add(start, n):
        for p in make_products(start, n, dealer):
            order = create_order(user=admin, lines=[(p.pk, p.variants.first().pk, 1)], data=DELIVERY)
            if p.dealer_id:
                DealerOrder.objects.create(order=order, dealer=dealer, reference=f"{order.number}-1")

    add(200, 4)
    small = queries(client, "/" + settings.ADMIN_URL + url)
    add(300, 25)
    assert queries(client, "/" + settings.ADMIN_URL + url) <= small + 1, f"{url} queries per row"
