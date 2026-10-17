import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import LoginCode
from apps.catalog.models import Product

User = get_user_model()
pytestmark = pytest.mark.django_db
PW = "pass12345!x"
CHALLENGE = reverse("accounts:two_factor")


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def user():
    return User.objects.create_user("jane@example.com", PW, first_name="Jane", email_verified=True, two_factor_enabled=True)


@pytest.fixture
def staff():
    return User.objects.create_superuser("boss@example.com", PW)


def password_login(client, email="jane@example.com", **extra):
    return client.post(reverse("accounts:login"), {"username": email, "password": PW, **extra})


def code():
    return re.search(r"\n(\d{6})\n", mail.outbox[-1].body).group(1)


def submit(client, value, **extra):
    return client.post(CHALLENGE, {"code": value, **extra})


def to_challenge(client, email="jane@example.com"):
    """Password-login then open the challenge page (which emails the code)."""
    password_login(client, email)
    client.get(CHALLENGE)


# ---- no 2FA: nothing changes ---------------------------------------------------
def test_user_without_2fa_signs_in_normally(client):
    User.objects.create_user("plain@example.com", PW, email_verified=True)
    assert password_login(client, "plain@example.com").status_code == 302
    assert client.get(reverse("accounts:account")).status_code == 200 and not mail.outbox


# ---- the gate ----------------------------------------------------------------------
def test_password_alone_is_not_enough(client, user):
    assert password_login(client).status_code == 302
    for url in ("/", reverse("accounts:account"), reverse("orders:list"), reverse("catalog:shop"), reverse("wishlist:list")):
        resp = client.get(url)
        assert resp.status_code == 302 and resp["Location"] == CHALLENGE, url


def test_gate_blocks_posts_and_htmx(client, user):
    password_login(client)
    p = Product.objects.first()
    resp = client.post(reverse("cart:add", args=[p.pk]))
    assert resp.status_code == 302 and resp["Location"] == CHALLENGE
    htmx = client.post(reverse("cart:add", args=[p.pk]), HTTP_HX_REQUEST="true")
    assert htmx.status_code == 204 and htmx["HX-Redirect"] == CHALLENGE


def test_gate_allows_static_logout_and_challenge(client, user):
    password_login(client)
    assert client.get(CHALLENGE).status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/offline/").status_code == 200
    client.post(reverse("accounts:logout"))
    assert "_auth_user_id" not in client.session


def test_challenge_emails_a_code_once_not_on_every_refresh(client, user):
    password_login(client)
    client.get(CHALLENGE); client.get(CHALLENGE); client.get(CHALLENGE)
    assert len(mail.outbox) == 1 and "sign-in code" in mail.outbox[0].subject


def test_code_is_stored_hashed(client, user):
    to_challenge(client)
    assert code() not in LoginCode.objects.get().code_hash


def test_challenge_requires_login(client):
    assert client.get(CHALLENGE)["Location"] == reverse("accounts:login")


# ---- verifying ---------------------------------------------------------------------
def test_correct_code_completes_login_and_returns_to_requested_page(client, user):
    password_login(client)
    client.get(reverse("orders:list"))  # remembered as the destination
    client.get(CHALLENGE)
    resp = submit(client, code())
    assert resp.status_code == 302 and resp["Location"] == reverse("orders:list")
    assert client.get(reverse("accounts:account")).status_code == 200


def test_wrong_code_rejected_and_locks_after_five(client, user):
    to_challenge(client)
    real = code()
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        submit(client, wrong)
    assert b"Too many wrong attempts" in submit(client, real).content  # even the right one is refused
    assert client.get(reverse("accounts:account")).status_code == 302


def test_expired_code_rejected(client, user):
    to_challenge(client)
    LoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert b"expired" in submit(client, code()).content


def test_code_is_single_use(client, user):
    to_challenge(client)
    c = code()
    submit(client, c)
    other = Client()
    password_login(other)
    other.get(CHALLENGE)
    assert b"isn" in submit(other, c).content or b"Too many" in submit(other, c).content


def test_one_users_code_cannot_unlock_another_session(client, user):
    other_user = User.objects.create_user("sam@example.com", PW, email_verified=True, two_factor_enabled=True)
    to_challenge(client)
    jane_code = code()
    sam = Client()
    to_challenge(sam, "sam@example.com")
    assert b"isn" in submit(sam, jane_code).content or b"Too many" in submit(sam, jane_code).content
    assert other_user.pk


def test_resend_cooldown_cap_and_invalidates_old_code(client, user):
    to_challenge(client)
    old = code()
    assert b"Please wait" in client.post(reverse("accounts:two_factor_resend"), follow=True).content
    assert len(mail.outbox) == 1
    for _ in range(3):
        LoginCode.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))
        client.post(reverse("accounts:two_factor_resend"))
    assert len(mail.outbox) == 4
    LoginCode.objects.update(last_sent_at=timezone.now() - timedelta(seconds=61))
    client.post(reverse("accounts:two_factor_resend"))
    assert len(mail.outbox) == 4  # capped
    new = code()
    if new != old:
        assert b"isn" in submit(client, old).content
    assert submit(client, new).status_code == 302


def test_verify_attempts_are_rate_limited(client, user):
    to_challenge(client)
    statuses = [submit(client, "000000").status_code for _ in range(32)]
    assert statuses[-1] == 429


# ---- password step still protected by axes ---------------------------------------------
def test_axes_lockout_still_guards_password_step(client, user):
    for _ in range(5):
        client.post(reverse("accounts:login"), {"username": "jane@example.com", "password": "wrong"})
    assert password_login(client).status_code == 429


# ---- remember this device ----------------------------------------------------------------
def test_remember_device_skips_next_challenge(client, user):
    to_challenge(client)
    resp = submit(client, code(), remember="on")
    assert "bm_trusted" in resp.cookies and resp.cookies["bm_trusted"]["httponly"]
    client.post(reverse("accounts:logout"))
    mail.outbox.clear()
    assert password_login(client).status_code == 302
    assert client.get(reverse("accounts:account")).status_code == 200  # no second challenge
    assert not mail.outbox


def test_without_remember_every_login_is_challenged(client, user):
    to_challenge(client)
    resp = submit(client, code())
    assert "bm_trusted" not in resp.cookies
    client.post(reverse("accounts:logout"))
    password_login(client)
    assert client.get(reverse("accounts:account"))["Location"] == CHALLENGE


def test_password_change_revokes_trusted_devices(client, user):
    to_challenge(client)
    submit(client, code(), remember="on")
    cookie = client.cookies["bm_trusted"].value
    user.set_password("Another-Pass-9981"); user.save()
    fresh = Client()
    fresh.cookies["bm_trusted"] = cookie
    fresh.post(reverse("accounts:login"), {"username": "jane@example.com", "password": "Another-Pass-9981"})
    assert fresh.get(reverse("accounts:account"))["Location"] == CHALLENGE


def test_trusted_cookie_of_another_user_is_ignored(client, user):
    to_challenge(client)
    submit(client, code(), remember="on")
    stolen = client.cookies["bm_trusted"].value
    User.objects.create_user("sam@example.com", PW, email_verified=True, two_factor_enabled=True)
    other = Client()
    other.cookies["bm_trusted"] = stolen
    other.post(reverse("accounts:login"), {"username": "sam@example.com", "password": PW})
    assert other.get(reverse("accounts:account"))["Location"] == CHALLENGE


def test_tampered_trusted_cookie_is_ignored(client, user):
    c = Client()
    c.cookies["bm_trusted"] = "1:garbage:signature"
    c.post(reverse("accounts:login"), {"username": "jane@example.com", "password": PW})
    assert c.get(reverse("accounts:account"))["Location"] == CHALLENGE


# ---- staff / admin -----------------------------------------------------------------------------
def test_admin_requires_code_even_without_opt_in(client, staff):
    assert not staff.two_factor_enabled
    assert client.post(reverse("admin:login"), {"username": staff.email, "password": PW, "next": reverse("admin:index")}).status_code == 302
    client.get("/")  # Jazzmin's login can bounce to the storefront home first; that must not become the destination
    resp = client.get(reverse("admin:index"))
    assert resp.status_code == 302 and resp["Location"] == CHALLENGE
    client.get(CHALLENGE)
    done = submit(client, code())
    assert done["Location"] == reverse("admin:index")
    assert client.get(reverse("admin:index")).status_code == 200


def test_staff_have_no_remember_option_and_are_never_trusted(client, staff):
    client.force_login(staff)
    page = client.get(CHALLENGE)
    assert b"Don&#x27;t ask again" not in page.content and b"remember" not in page.content
    resp = submit(client, code(), remember="on")
    assert "bm_trusted" not in resp.cookies


def test_stale_staff_session_without_flag_is_forced_to_challenge(client, staff):
    client.force_login(staff)  # session predates the 2FA flag
    assert client.get(reverse("admin:index"))["Location"] == CHALLENGE


def test_staff_still_blocked_from_storefront_login(client, staff):
    resp = password_login(client, staff.email)
    assert resp.status_code == 200 and b"Staff accounts" in resp.content


# ---- security page ----------------------------------------------------------------------------
def test_customer_can_turn_on_and_off(client):
    u = User.objects.create_user("opt@example.com", PW, email_verified=True)
    client.force_login(u)
    assert client.get(reverse("accounts:security")).status_code == 200
    client.post(reverse("accounts:two_factor_enable"))
    u.refresh_from_db()
    assert u.two_factor_enabled
    assert client.get(reverse("accounts:account")).status_code == 200  # current session stays usable
    bad = client.post(reverse("accounts:two_factor_disable"), {"password": "nope"})
    u.refresh_from_db()
    assert u.two_factor_enabled and b"isn" in bad.content
    client.post(reverse("accounts:two_factor_disable"), {"password": PW})
    u.refresh_from_db()
    assert not u.two_factor_enabled


def test_unverified_email_cannot_enable(client):
    u = User.objects.create_user("unv@example.com", PW)
    client.force_login(u)
    client.post(reverse("accounts:two_factor_enable"))
    u.refresh_from_db()
    assert not u.two_factor_enabled


def test_staff_cannot_disable(client, staff):
    client.force_login(staff)
    s = client.session; s["2fa_verified"] = True; s.save()
    client.post(reverse("accounts:two_factor_disable"), {"password": PW})
    assert staff.requires_two_factor


def test_security_pages_require_login(client):
    for name in ("accounts:security",):
        assert client.get(reverse(name)).status_code == 302
    assert client.post(reverse("accounts:two_factor_enable")).status_code == 302


# ---- checkout interplay -------------------------------------------------------------------------
def test_checkout_confirmation_login_does_not_ask_for_a_second_code(client):
    User.objects.create_user("buyer@example.com", PW, email_verified=True, two_factor_enabled=True)
    p = Product.objects.first()
    client.post(reverse("cart:add", args=[p.pk]))
    client.post(reverse("orders:checkout"), {
        "email": "buyer@example.com", "full_name": "B", "phone": "01712345678", "address": "x", "city": "Dhaka", "delivery_area": "dhaka"})
    client.post(reverse("orders:confirm"), {"code": code()})
    assert client.get(reverse("orders:list")).status_code == 200  # not bounced to the 2FA challenge
