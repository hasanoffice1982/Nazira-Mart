import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse

from apps.accounts import addresses as book
from apps.accounts.models import PresentAddress, ShippingAddress
from apps.catalog.models import Product
from apps.orders.models import Order

User = get_user_model()
pytestmark = pytest.mark.django_db
HX = {"HTTP_HX_REQUEST": "true"}
ADDR = {"label": "home", "full_name": "Rafi", "phone": "01712345678", "address": "House 1, Road 2", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def user():
    return User.objects.create_user("a@example.com", "pass12345!x", first_name="A", email_verified=True)


@pytest.fixture
def other():
    return User.objects.create_user("b@example.com", "pass12345!x", first_name="B", email_verified=True)


def mk(user, **kw):
    return book.save_shipping(user, ShippingAddress(**{**ADDR, **kw}))


# ---- rules ------------------------------------------------------------------
def test_first_address_becomes_default(user):
    assert mk(user).is_default and not mk(user, label="office").is_default


def test_only_one_default_at_a_time(user):
    a, b = mk(user), mk(user, label="office")
    book.set_default(user, b)
    a.refresh_from_db(); b.refresh_from_db()
    assert (a.is_default, b.is_default) == (False, True)
    book.save_shipping(user, a, make_default=True)
    b.refresh_from_db()
    assert not b.is_default and user.shipping_addresses.filter(is_default=True).count() == 1


def test_deleting_default_promotes_newest(user):
    a, b, c = mk(user), mk(user, label="office"), mk(user, label="other")
    book.delete_shipping(user, a)
    c.refresh_from_db(); b.refresh_from_db()
    assert c.is_default and not b.is_default


def test_deleting_last_address_leaves_none(user):
    book.delete_shipping(user, mk(user))
    assert not user.shipping_addresses.exists()


def test_ten_address_cap(user):
    for _ in range(10):
        mk(user)
    with pytest.raises(book.AddressLimitError):
        mk(user)
    assert not book.can_add(user)


# ---- views -----------------------------------------------------------------
def test_pages_require_login(client):
    for name in ("accounts:addresses", "accounts:address_present", "accounts:address_new"):
        assert client.get(reverse(name)).status_code == 302
    assert client.post(reverse("accounts:address_new"), ADDR).status_code == 302


def test_add_edit_delete_via_htmx(client, user):
    client.force_login(user)
    page = client.get(reverse("accounts:addresses"))
    assert page.status_code == 200 and b"No shipping addresses yet" in page.content
    resp = client.post(reverse("accounts:address_new"), ADDR, **HX)
    assert resp.status_code == 200 and b"House 1, Road 2" in resp.content
    addr = user.shipping_addresses.get()
    client.post(reverse("accounts:address_edit", args=[addr.pk]), {**ADDR, "address": "New Street 9"}, **HX)
    addr.refresh_from_db()
    assert addr.address == "New Street 9" and addr.is_default
    client.post(reverse("accounts:address_delete", args=[addr.pk]), **HX)
    assert not user.shipping_addresses.exists()


def test_invalid_address_re_renders_form_with_errors(client, user):
    client.force_login(user)
    resp = client.post(reverse("accounts:address_new"), {**ADDR, "phone": "123"}, **HX)
    assert b"valid Bangladeshi" in resp.content and not user.shipping_addresses.exists()


def test_cannot_touch_someone_elses_address(client, user, other):
    theirs = mk(other)
    client.force_login(user)
    assert client.get(reverse("accounts:address_edit", args=[theirs.pk]), **HX).status_code == 404
    assert client.post(reverse("accounts:address_edit", args=[theirs.pk]), ADDR, **HX).status_code == 404
    assert client.post(reverse("accounts:address_delete", args=[theirs.pk]), **HX).status_code == 404
    assert client.post(reverse("accounts:address_default", args=[theirs.pk]), **HX).status_code == 404
    assert ShippingAddress.objects.filter(pk=theirs.pk).exists()


def test_other_users_addresses_not_listed(client, user, other):
    mk(other, address="SECRET STREET")
    client.force_login(user)
    assert b"SECRET STREET" not in client.get(reverse("accounts:addresses")).content


def test_make_default_action(client, user):
    mk(user); b = mk(user, label="office")
    client.force_login(user)
    client.post(reverse("accounts:address_default", args=[b.pk]), **HX)
    b.refresh_from_db()
    assert b.is_default


def test_present_address_single_record_and_validation(client, user):
    client.force_login(user)
    data = {k: v for k, v in ADDR.items() if k != "label"}
    assert b"valid Bangladeshi" in client.post(reverse("accounts:address_present"), {**data, "phone": "1"}, **HX).content
    client.post(reverse("accounts:address_present"), data, **HX)
    client.post(reverse("accounts:address_present"), {**data, "city": "Chattogram"}, **HX)
    assert PresentAddress.objects.filter(user=user).count() == 1 and user.present_address.city == "Chattogram"


def test_add_beyond_cap_via_view_is_rejected(client, user):
    for _ in range(10):
        mk(user)
    client.force_login(user)
    client.post(reverse("accounts:address_new"), ADDR, **HX)
    assert user.shipping_addresses.count() == 10


# ---- checkout picker --------------------------------------------------------
def add_to_cart(client):
    p = Product.objects.first()
    client.post(reverse("cart:add", args=[p.pk]))
    return p


def test_checkout_preselects_default_and_lists_choices(client, user):
    mk(user, address="Home St"); mk(user, label="office", address="Office St")
    client.force_login(user)
    add_to_cart(client)
    html = client.get(reverse("orders:checkout")).content.decode()
    assert "Home St" in html and "Office St" in html and "Deliver to a new address" in html
    assert html.count("checked") >= 1


def test_checkout_with_saved_shipping_address(client, user):
    mk(user, address="Home St"); office = mk(user, label="office", address="Office St", delivery_area="outside")
    client.force_login(user)
    add_to_cart(client)
    resp = client.post(reverse("orders:checkout"), {"address_choice": f"ship:{office.pk}"})
    order = Order.objects.get()
    assert resp.status_code == 302 and order.address == "Office St" and order.delivery_area == "outside"
    assert order.delivery_charge == 120


def test_checkout_with_present_address_sets_billing(client, user):
    PresentAddress.objects.create(user=user, **{k: v for k, v in ADDR.items() if k != "label"}, )
    client.force_login(user)
    add_to_cart(client)
    client.post(reverse("orders:checkout"), {"address_choice": "present"})
    order = Order.objects.get()
    assert order.address == "House 1, Road 2" and order.billing_address == "House 1, Road 2" and order.billing_name == "Rafi"


def test_billing_uses_present_address_when_shipping_differs(client, user):
    PresentAddress.objects.create(user=user, full_name="Home Person", phone="01712345678", address="Present Rd", city="Dhaka")
    ship = mk(user, address="Gift Rd", full_name="Friend")
    client.force_login(user)
    add_to_cart(client)
    client.post(reverse("orders:checkout"), {"address_choice": f"ship:{ship.pk}"})
    order = Order.objects.get()
    assert order.address == "Gift Rd" and order.billing_address == "Present Rd" and order.billing_name == "Home Person"


def test_new_address_requires_fields_and_can_be_saved(client, user):
    mk(user)
    client.force_login(user)
    add_to_cart(client)
    bad = client.post(reverse("orders:checkout"), {"address_choice": "new"})
    assert bad.status_code == 200 and not Order.objects.exists()
    good = {"address_choice": "new", "full_name": "N", "phone": "01812345678", "address": "Brand New Rd", "city": "Sylhet", "delivery_area": "outside", "save_address": "on"}
    client.post(reverse("orders:checkout"), good)
    assert Order.objects.get().address == "Brand New Rd"
    assert user.shipping_addresses.filter(address="Brand New Rd").exists()


def test_cannot_check_out_to_another_users_address(client, user, other):
    theirs = mk(other, address="Their Rd")
    mk(user)
    client.force_login(user)
    add_to_cart(client)
    resp = client.post(reverse("orders:checkout"), {"address_choice": f"ship:{theirs.pk}"})
    assert resp.status_code == 200 and not Order.objects.exists()


def test_old_orders_keep_snapshot_after_address_edit_or_delete(client, user):
    addr = mk(user, address="Original Rd")
    client.force_login(user)
    add_to_cart(client)
    client.post(reverse("orders:checkout"), {"address_choice": f"ship:{addr.pk}"})
    client.post(reverse("accounts:address_edit", args=[addr.pk]), {**ADDR, "address": "Changed Rd"}, **HX)
    assert Order.objects.get().address == "Original Rd"
    client.post(reverse("accounts:address_delete", args=[addr.pk]), **HX)
    assert Order.objects.get().address == "Original Rd"


def test_user_without_addresses_sees_plain_form(client, user):
    client.force_login(user)
    add_to_cart(client)
    html = client.get(reverse("orders:checkout")).content.decode()
    assert "address_choice" not in html and "save_address" in html


def test_guest_checkout_saves_first_address_as_default(client):
    add_to_cart(client)
    client.post(reverse("orders:checkout"), {"email": "g@example.com", **{k: v for k, v in ADDR.items() if k != "label"}})
    u = User.objects.get(email="g@example.com")
    saved = u.shipping_addresses.get()
    assert saved.is_default and saved.address == "House 1, Road 2"
    assert Order.objects.get().billing_address == "House 1, Road 2"
