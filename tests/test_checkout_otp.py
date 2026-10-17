import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Product
from apps.orders import otp as otp_service
from apps.orders.models import CheckoutOTP, Order

User = get_user_model()
pytestmark = pytest.mark.django_db
FORM = {"email": "Guest@Example.com", "full_name": "Guest Buyer", "phone": "01712345678",
        "address": "House 1, Road 2", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


def code_from_mail(index=-1):
    return re.search(r"\n(\d{6})\n", mail.outbox[index].body).group(1)


def start(client, qty=2, **over):
    p = Product.objects.get(art_type="headphones")
    for _ in range(qty):
        client.post(reverse("cart:add", args=[p.pk]))
    return p, client.post(reverse("orders:checkout"), {**FORM, **over})


def confirm(client, code):
    return client.post(reverse("orders:confirm"), {"code": code})


def test_guest_checkout_creates_nothing_until_code_entered(client):
    p, resp = start(client)
    assert resp.status_code == 302 and resp["Location"] == reverse("orders:confirm")
    assert not Order.objects.exists() and not User.objects.filter(email="guest@example.com").exists()
    p.refresh_from_db()
    assert p.stock == 40  # untouched
    assert len(mail.outbox) == 1 and "confirmation code" in mail.outbox[0].subject


def test_code_is_stored_hashed(client):
    start(client)
    code = code_from_mail()
    assert code not in CheckoutOTP.objects.get().code_hash


def test_correct_code_places_order_creates_account_logs_in(client):
    p, _ = start(client)
    resp = confirm(client, code_from_mail())
    order = Order.objects.get()
    assert resp.status_code == 302 and resp["Location"] == reverse("orders:success", args=[order.number])
    user = User.objects.get(email="guest@example.com")
    assert order.user == user and user.email_verified and not user.has_usable_password()
    assert str(user.pk) == client.session["_auth_user_id"]
    p.refresh_from_db()
    assert p.stock == 38
    assert client.get(reverse("cart:add", args=[p.pk])).status_code == 405  # cart drawer partial is POST-only
    assert not user.cart.items.exists() if hasattr(user, "cart") else True  # guest cart was not merged back in
    assert client.get(order.get_absolute_url()).status_code == 200


def test_confirmation_email_contains_set_password_link_that_works(client, django_capture_on_commit_callbacks):
    start(client)
    with django_capture_on_commit_callbacks(execute=True):  # the email is sent after the transaction commits
        confirm(client, code_from_mail())
    body = mail.outbox[-1].body
    assert "Order BM-" in body and "Choose a password" in body
    link = re.search(r"http://testserver(/accounts/password/reset/\S+)", body).group(1)
    client.logout()  # the customer opens the link later, signed out
    page = client.get(link, follow=True)
    assert b"Choose a new password" in page.content
    done = client.post(page.request["PATH_INFO"], {"new_password1": "Brand-new-Pass-77", "new_password2": "Brand-new-Pass-77"})
    assert done.status_code == 302
    assert client.post(reverse("accounts:login"), {"username": "guest@example.com", "password": "Brand-new-Pass-77"}).status_code == 302


def test_forgot_password_works_for_accounts_without_a_password(client):
    start(client)
    confirm(client, code_from_mail())
    mail.outbox.clear()
    client.logout()
    client.post(reverse("accounts:password_reset"), {"email": "guest@example.com"})
    assert len(mail.outbox) == 1


def test_wrong_code_rejected_and_locks_after_five(client):
    start(client)
    real = code_from_mail()
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert b"isn" in confirm(client, wrong).content or b"Too many" in confirm(client, wrong).content
    assert b"Too many wrong attempts" in confirm(client, real).content  # even the right code is refused now
    assert not Order.objects.exists()


def test_expired_code_rejected(client):
    start(client)
    CheckoutOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert b"expired" in confirm(client, code_from_mail()).content
    assert not Order.objects.exists()


def test_code_cannot_be_reused(client):
    start(client)
    code = code_from_mail()
    confirm(client, code)
    client.logout()
    assert confirm(client, code).status_code == 302  # no pending checkout in session -> back to checkout
    assert Order.objects.count() == 1


def test_resend_has_cooldown_and_cap(client):
    start(client)
    first = code_from_mail()
    resp = client.post(reverse("orders:resend"), follow=True)
    assert b"Please wait" in resp.content and len(mail.outbox) == 1
    for i in range(3):
        CheckoutOTP.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))
        client.post(reverse("orders:resend"))
    assert len(mail.outbox) == 4
    CheckoutOTP.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))
    client.post(reverse("orders:resend"))
    assert len(mail.outbox) == 4  # capped at 3 resends
    assert code_from_mail(0) != "" and first


def test_resend_invalidates_the_old_code(client):
    start(client)
    old = code_from_mail()
    CheckoutOTP.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))
    client.post(reverse("orders:resend"))
    new = code_from_mail()
    if old != new:
        assert b"isn" in confirm(client, old).content
    assert confirm(client, new).status_code == 302 and Order.objects.exists()


def test_stock_rechecked_at_confirmation(client):
    p, _ = start(client, qty=2)
    Product.objects.filter(pk=p.pk).update(stock=1)  # sold elsewhere while waiting
    resp = confirm(client, code_from_mail())
    assert b"only 1" in resp.content and not Order.objects.exists()
    assert not User.objects.filter(email="guest@example.com").exists()  # rolled back with the order


def test_existing_customer_is_logged_in_and_order_attached(client):
    owner = User.objects.create_user("guest@example.com", "x-Pass-1234567", first_name="Old")
    start(client)
    confirm(client, code_from_mail())
    assert Order.objects.get().user == owner
    owner.refresh_from_db()
    assert owner.email_verified and owner.has_usable_password()  # existing password untouched
    assert User.objects.filter(email__iexact="guest@example.com").count() == 1


def test_staff_email_cannot_be_used_for_guest_orders(client):
    User.objects.create_superuser("guest@example.com", "x-Pass-1234567")
    _, resp = start(client)
    assert resp.status_code == 200 and b"different email" in resp.content and not CheckoutOTP.objects.exists()
    assert len(mail.outbox) == 0


def test_verified_customer_skips_the_code(client):
    u = User.objects.create_user("v@example.com", "x-Pass-1234567", email_verified=True)
    client.force_login(u)
    p = Product.objects.first()
    client.post(reverse("cart:add", args=[p.pk]))
    data = {k: v for k, v in FORM.items() if k != "email"}
    resp = client.post(reverse("orders:checkout"), data)
    assert Order.objects.get().user == u and resp["Location"].startswith("/orders/BM-")
    assert any("confirmed" in m.subject for m in mail.outbox)  # confirmation email sent
    assert not CheckoutOTP.objects.exists()


def test_checkout_otp_emails_are_rate_limited_per_address(client):
    p = Product.objects.first()
    client.post(reverse("cart:add", args=[p.pk]))
    codes = [client.post(reverse("orders:checkout"), FORM).status_code for _ in range(7)]
    assert codes[:5] == [302] * 5 and codes[5:] == [429, 429]


def test_honeypot_on_checkout(client):
    p = Product.objects.first()
    client.post(reverse("cart:add", args=[p.pk]))
    resp = client.post(reverse("orders:checkout"), {**FORM, "hp_contact": "spam"})
    assert resp.status_code == 200 and not CheckoutOTP.objects.exists() and len(mail.outbox) == 0


def test_confirm_page_without_pending_checkout_redirects(client):
    assert client.get(reverse("orders:confirm")).status_code == 302


def test_mask_email():
    assert otp_service.mask_email("rafi@example.com") == "ra••@example.com"
    assert otp_service.purge_expired() == 0
