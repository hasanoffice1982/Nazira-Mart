import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product

User = get_user_model()
pytestmark = pytest.mark.django_db
PW = "pass12345!x"
NEW_PW = "Brand-new-Pass-77"


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def user():
    return User.objects.create_user("jane@example.com", PW, first_name="Jane", email_verified=True)


def login(client, email="jane@example.com", pw=PW, **extra):
    return client.post(reverse("accounts:login"), {"username": email, "password": pw, **extra})


# ---- login ---------------------------------------------------------------
def test_login_is_case_insensitive(client, user):
    assert login(client, "JANE@Example.COM").status_code == 302
    assert "_auth_user_id" in client.session


def test_remember_me_controls_session_expiry(client, user):
    login(client)  # checkbox absent => not remembered
    assert client.session.get_expire_at_browser_close()
    client.logout()
    login(client, remember_me="on")
    assert not client.session.get_expire_at_browser_close()


def test_login_lockout_after_repeated_failures(client, user):
    for _ in range(5):
        login(client, pw="wrong-password")
    resp = login(client)  # correct password, but axes has locked this username+IP
    assert resp.status_code == 429 and b"Too many attempts" in resp.content
    assert "_auth_user_id" not in client.session


def test_lockout_is_per_username_not_global(client, user):
    for _ in range(5):
        login(client, pw="wrong-password")
    other = User.objects.create_user("other@example.com", PW, email_verified=True)
    assert login(client, "other@example.com").status_code == 302  # different account still works
    assert other.pk


def test_lockout_ignores_email_case(client, user):
    for i in range(5):
        login(client, ("JANE@example.com", "jane@EXAMPLE.com")[i % 2], pw="nope")
    assert login(client).status_code == 429


def test_successful_login_resets_failure_counter(client, user):
    for _ in range(3):
        login(client, pw="nope")
    assert login(client).status_code == 302
    client.logout()
    for _ in range(4):
        login(client, pw="nope")
    assert login(client).status_code == 302  # counter was reset, not locked


def test_open_redirect_blocked(client, user):
    resp = login(client, next="https://evil.example/")
    assert resp.status_code == 302 and "evil.example" not in resp["Location"]


# ---- registration + verification ----------------------------------------
def signup(client, email="new@example.com"):
    return client.post(reverse("accounts:register"), {
        "first_name": "New", "email": email, "phone": "", "password1": NEW_PW, "password2": NEW_PW})


def test_signup_sends_verification_and_verifies(client):
    assert signup(client).status_code == 302
    user = User.objects.get(email="new@example.com")
    assert not user.email_verified and len(mail.outbox) == 1
    link = re.search(r"http://testserver(/accounts/verify/\S+)", mail.outbox[0].body).group(1)
    assert client.get(link).status_code == 200
    user.refresh_from_db()
    assert user.email_verified and user.email_verified_at


def test_duplicate_email_case_insensitive(client, user):
    signup(client, "JANE@example.com")
    assert User.objects.filter(email__iexact="jane@example.com").count() == 1


def test_bad_verify_token(client):
    assert client.get(reverse("accounts:verify", args=["garbage"])).status_code == 400


def test_verify_token_dies_when_email_changes(client):
    signup(client)
    user = User.objects.get(email="new@example.com")
    from apps.accounts.services import make_verify_token

    token = make_verify_token(user)
    User.objects.filter(pk=user.pk).update(email="changed@example.com")
    assert client.get(reverse("accounts:verify", args=[token])).status_code == 400


def test_unverified_user_must_confirm_checkout_by_code(client):
    signup(client)
    client.post(reverse("cart:add", args=[Product.objects.first().pk]))
    mail.outbox.clear()
    resp = client.post(reverse("orders:checkout"), {"full_name": "N", "phone": "01712345678", "address": "x", "city": "Dhaka", "delivery_area": "dhaka"})
    assert resp.status_code == 302 and resp["Location"] == reverse("orders:confirm")
    assert len(mail.outbox) == 1


def test_resend_verification_throttled(client):
    signup(client)
    for _ in range(5):
        client.post(reverse("accounts:verify_resend"))
    before = len(mail.outbox)
    client.post(reverse("accounts:verify_resend"))
    assert len(mail.outbox) == before


# ---- password reset -------------------------------------------------------
def test_password_reset_full_flow(client, user):
    resp = client.post(reverse("accounts:password_reset"), {"email": "JANE@example.com"})
    assert resp.status_code == 302 and len(mail.outbox) == 1
    path = re.search(r"http://testserver(/accounts/password/reset/\S+)", mail.outbox[0].body).group(1)
    page = client.get(path, follow=True)  # Django swaps token into session then redirects
    assert page.status_code == 200 and b"Choose a new password" in page.content
    done = client.post(page.request["PATH_INFO"], {"new_password1": NEW_PW, "new_password2": NEW_PW})
    assert done.status_code == 302
    assert login(client, pw=NEW_PW).status_code == 302
    assert len(mail.outbox) == 2  # "password changed" notice
    # link is single-use
    assert b"Link expired" in client.get(path, follow=True).content


def test_reset_does_not_reveal_whether_account_exists(client, user):
    known = client.post(reverse("accounts:password_reset"), {"email": "jane@example.com"})
    unknown = client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 302 and known["Location"] == unknown["Location"]
    assert len(mail.outbox) == 1


def test_reset_emails_rate_limited_per_address(client, user):
    codes = [client.post(reverse("accounts:password_reset"), {"email": "jane@example.com"}).status_code for _ in range(5)]
    assert codes == [302, 302, 302, 429, 429] and len(mail.outbox) == 3


def test_reset_marks_email_verified(client):
    u = User.objects.create_user("v@example.com", PW)
    client.post(reverse("accounts:password_reset"), {"email": "v@example.com"})
    path = re.search(r"http://testserver(/accounts/password/reset/\S+)", mail.outbox[0].body).group(1)
    page = client.get(path, follow=True)
    client.post(page.request["PATH_INFO"], {"new_password1": NEW_PW, "new_password2": NEW_PW})
    u.refresh_from_db()
    assert u.email_verified


# ---- change password / profile -------------------------------------------
def test_password_change_keeps_session_and_notifies(client, user):
    client.force_login(user)
    resp = client.post(reverse("accounts:password_change"), {"old_password": PW, "new_password1": NEW_PW, "new_password2": NEW_PW})
    assert resp.status_code == 302 and client.get(reverse("accounts:account")).status_code == 200
    assert len(mail.outbox) == 1
    user.refresh_from_db()
    assert user.check_password(NEW_PW)


def test_password_change_invalidates_other_sessions(client, client_class=None, user=None):
    from django.test import Client

    u = User.objects.create_user("multi@example.com", PW, email_verified=True)
    other = Client()
    other.force_login(u)
    client.force_login(u)
    client.post(reverse("accounts:password_change"), {"old_password": PW, "new_password1": NEW_PW, "new_password2": NEW_PW})
    assert other.get(reverse("accounts:account")).status_code == 302  # kicked to login


def test_weak_passwords_rejected(client, user):
    client.force_login(user)
    client.post(reverse("accounts:password_change"), {"old_password": PW, "new_password1": "short1", "new_password2": "short1"})
    user.refresh_from_db()
    assert user.check_password(PW)


def test_profile_update_validates_phone(client, user):
    client.force_login(user)
    bad = client.post(reverse("accounts:profile"), {"first_name": "J", "last_name": "D", "phone": "123"})
    assert b"valid Bangladeshi" in bad.content
    client.post(reverse("accounts:profile"), {"first_name": "Janet", "last_name": "D", "phone": "01712345678"})
    user.refresh_from_db()
    assert user.first_name == "Janet" and user.phone == "01712345678"


def test_account_pages_require_login(client):
    for name in ("accounts:account", "accounts:profile", "accounts:password_change"):
        assert client.get(reverse(name)).status_code == 302
    assert client.post(reverse("accounts:verify_resend")).status_code == 302


def test_superuser_is_verified_by_default():
    assert User.objects.create_superuser("root@example.com", PW).email_verified


# ---- honeypot ----------------------------------------------------------------
def test_honeypot_blocks_bot_signup(client):
    resp = client.post(reverse("accounts:register"), {
        "first_name": "Bot", "email": "bot@example.com", "phone": "", "password1": NEW_PW, "password2": NEW_PW, "hp_contact": "http://spam"})
    assert resp.status_code == 200 and not User.objects.filter(email="bot@example.com").exists()


def test_honeypot_blocks_bot_login(client, user):
    assert login(client, hp_contact="x").status_code == 200 and "_auth_user_id" not in client.session


def test_honeypot_blocks_bot_password_reset(client, user):
    resp = client.post(reverse("accounts:password_reset"), {"email": "jane@example.com", "hp_contact": "x"})
    assert resp.status_code == 200 and len(mail.outbox) == 0


def test_honeypot_field_is_hidden_in_markup(client):
    html = client.get(reverse("accounts:register")).content.decode()
    assert 'name="hp_contact"' in html and 'class="hp"' in html and 'tabindex="-1"' in html


def test_newsletter_honeypot_stores_nothing(client):
    from apps.core.models import NewsletterSubscriber

    client.post(reverse("core:newsletter"), {"email": "bot@example.com", "hp_contact": "x"})
    assert not NewsletterSubscriber.objects.exists()


# ---- ratelimit -----------------------------------------------------------------
def test_signup_rate_limited_per_ip(client):
    codes = [signup(client, f"u{i}@example.com").status_code for i in range(12)]
    assert codes[:10].count(302) == 10 and codes[10:] == [429, 429]


def test_newsletter_rate_limited_and_htmx_gets_toast(client):
    for i in range(5):
        client.post(reverse("core:newsletter"), {"email": f"n{i}@example.com"})
    resp = client.post(reverse("core:newsletter"), {"email": "n9@example.com"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 429 and "toast" in resp["HX-Trigger"]


def test_429_page_renders_for_normal_requests(client):
    for i in range(5):
        client.post(reverse("core:newsletter"), {"email": f"n{i}@example.com"})
    resp = client.post(reverse("core:newsletter"), {"email": "x@example.com"})
    assert resp.status_code == 429 and resp["Retry-After"] == "60" and b"Too many attempts" in resp.content


# ---- CSP -------------------------------------------------------------------------
def test_csp_header_is_strict_for_storefront(client):
    csp = client.get("/")["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


def test_csp_relaxed_only_for_admin(client):
    csp = client.get(reverse("admin:login"))["Content-Security-Policy"]
    assert "'unsafe-inline'" in csp.split("script-src")[1].split(";")[0]


def test_no_inline_handlers_in_storefront_html(client):
    import re

    for path in ("/", "/shop/", reverse("accounts:login")):
        html = client.get(path).content.decode()
        assert not re.search(r"\son(click|submit|change|input|load)=", html), path
        assert "hx-on" not in html
