import re
import shutil
import subprocess

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from apps.core.models import ContactMessage, Page
from apps.core.text import render_page_body
from apps.catalog.models import Product
from apps.orders.models import Order
from apps.orders.services import change_status, create_order

User = get_user_model()
pytestmark = pytest.mark.django_db
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1, Road 2", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def user():
    return User.objects.create_user("buyer@example.com", "pass12345!x", first_name="Buyer", email_verified=True)


@pytest.fixture
def order(user):
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    return create_order(user=user, lines=[(p.pk, 0, 1)], data=DELIVERY)


# ======================= service worker / PWA =======================
def sw(client):
    return client.get("/sw.js")


def test_sw_headers_and_scope(client):
    r = sw(client)
    assert r.status_code == 200 and r["Content-Type"].startswith("application/javascript")
    assert r["Cache-Control"] == "no-cache" and r["Service-Worker-Allowed"] == "/"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_rendered_sw_is_valid_javascript(client, tmp_path):
    f = tmp_path / "sw.js"
    f.write_bytes(sw(client).content)
    result = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr  # regression: autoescaped quotes once broke the file


def test_sw_precaches_current_assets_including_fonts_and_swiper(client):
    body = sw(client).content.decode()
    for needle in ("fonts.css", "narizmart.css", "app.css", "swiper-bundle.min.js", "swiper-bundle.min.css", "htmx.min.js", "dm-sans-latin-wght-normal.woff2", "app.js", "/offline/"):
        assert needle in body, needle


def test_sw_version_changes_with_release(client):
    with override_settings(APP_VERSION="release-1"):
        v1 = re.search(r'VERSION = "(\w+)"', sw(client).content.decode()).group(1)
    with override_settings(APP_VERSION="release-2"):
        v2 = re.search(r'VERSION = "(\w+)"', sw(client).content.decode()).group(1)
    assert v1 != v2


def test_sw_never_touches_private_pages_or_caches_html(client):
    body = sw(client).content.decode()
    assert "/admin" not in body and "manage-" not in body and "/accounts" not in body and "/orders" not in body and "/cart" not in body
    assert "HX-Request" in body and 'req.mode === "navigate"' in body
    assert "cache.put(req" in body and "MEDIA_LIMIT" in body and "SKIP_WAITING" in body


def test_production_mode_uses_cache_first_for_hashed_files(client, settings):
    settings.DEBUG = False
    assert "CONTENT_HASHED = true" in sw(client).content.decode()
    settings.DEBUG = True
    assert "CONTENT_HASHED = false" in sw(client).content.decode()


def test_offline_page_is_standalone_and_has_no_user_data(client, user):
    client.force_login(user)
    html = client.get("/offline/").content.decode()
    assert "csrf" not in html.lower() and "Hi, Buyer" not in html and "cartCount" not in html and "buyer@example.com" not in html


def test_manifest_valid(client):
    data = client.get("/manifest.webmanifest").json()
    assert data["display"] == "standalone" and data["start_url"] and {i["sizes"] for i in data["icons"]} >= {"192x192", "512x512"}
    assert any(i.get("purpose") == "maskable" for i in data["icons"])


# ======================= pages =======================
def test_default_pages_are_seeded_and_published(client):
    for slug in ("help-center", "shipping-delivery", "returns-exchanges", "privacy-policy", "terms-and-conditions"):
        r = client.get(reverse("core:page", args=[slug]))
        assert r.status_code == 200 and b"<h1>" in r.content


def test_pages_have_no_inline_script_and_valid_csp(client):
    r = client.get(reverse("core:page", args=["privacy-policy"]))
    assert "<script>" not in r.content.decode().replace("<script src", "")
    assert "script-src 'self'" in r["Content-Security-Policy"]


def test_unpublished_page_is_404_and_not_in_footer(client):
    Page.objects.filter(slug="privacy-policy").update(is_published=False)
    assert client.get(reverse("core:page", args=["privacy-policy"])).status_code == 404
    assert b"Privacy Policy" not in client.get("/").content


def test_unknown_page_is_404(client):
    assert client.get(reverse("core:page", args=["nope"])).status_code == 404


def test_footer_links_point_to_real_pages(client):
    html = client.get("/").content.decode()
    for slug in ("help-center", "shipping-delivery", "returns-exchanges", "privacy-policy", "terms-and-conditions"):
        assert f"/p/{slug}/" in html
    assert "/contact/" in html


def test_page_body_renderer_escapes_everything():
    html = render_page_body('## Title <script>alert(1)</script>\n\nHello **world** <b>x</b>\n- one\n- two <img src=x onerror=alert(1)>')
    assert "<script>" not in html and "<b>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html  # shown as harmless text, never executed
    assert "<h2>" in html and "<strong>world</strong>" in html and html.count("<li>") == 2


def test_page_body_renderer_links_and_paragraphs():
    html = render_page_body("Visit https://example.com now\nsecond line\n\nNew paragraph")
    assert 'href="https://example.com"' in html and 'rel="nofollow"' in html and html.count("<p>") == 2 and "<br>" in html


def test_admin_edit_changes_page(client):
    Page.objects.filter(slug="help-center").update(body="Custom help text here")
    assert b"Custom help text here" in client.get(reverse("core:page", args=["help-center"])).content


# ======================= contact =======================
def post_contact(client, **over):
    data = {"name": "Rafi", "email": "rafi@example.com", "message": "Where is my order? Thanks a lot.", **over}
    return client.post(reverse("core:contact"), data)


def test_contact_saves_and_emails_support(client, settings):
    settings.SUPPORT_EMAIL = "help@example.com"
    resp = post_contact(client, order_number="nm-1234", phone="01712345678")
    assert resp.status_code == 302 and b"Message sent" in client.get(resp["Location"]).content
    msg = ContactMessage.objects.get()
    assert msg.order_number == "NM-1234"
    sent = mail.outbox[0]
    assert sent.to == ["help@example.com"] and sent.reply_to == ["rafi@example.com"] and "NM-1234" in sent.subject


def test_contact_validation(client):
    assert post_contact(client, message="short").status_code == 200 and not ContactMessage.objects.exists()
    assert post_contact(client, email="nope").status_code == 200 and not ContactMessage.objects.exists()


def test_contact_honeypot_and_header_injection(client):
    assert post_contact(client, hp_contact="spam").status_code == 200 and not ContactMessage.objects.exists()
    resp = post_contact(client, name="Rafi\nBcc: victim@example.com")
    assert not any("victim@example.com" in str(m.message()) for m in mail.outbox) or resp.status_code in (200,)


def test_contact_rate_limited_per_ip_and_email(client):
    codes = [post_contact(client).status_code for _ in range(5)]
    assert codes[:3] == [302, 302, 302] and codes[3:] == [429, 429]


def test_contact_survives_mail_failure(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "127.0.0.1"
    settings.EMAIL_PORT = 1  # connection refused
    assert post_contact(client).status_code == 302 and ContactMessage.objects.count() == 1


def test_contact_prefills_for_signed_in_user(client, user):
    client.force_login(user)
    html = client.get(reverse("core:contact") + "?order=NM-77").content.decode()
    assert "buyer@example.com" in html and "NM-77" in html


# ======================= order status emails =======================
def run(order, status, django_capture_on_commit_callbacks, **kw):
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, status, **kw)


def test_every_status_change_emails_the_customer(order, django_capture_on_commit_callbacks):
    expected = {"confirmed": "is confirmed", "processing": "packing", "shipped": "on its way", "delivered": "was delivered"}
    for status, text in expected.items():
        run(order, status, django_capture_on_commit_callbacks)
        assert len(mail.outbox) == 1 and text in mail.outbox[0].subject and mail.outbox[0].to == ["buyer@example.com"]
        assert order.number in mail.outbox[0].subject


def test_shipped_email_has_tracking_and_cod_amount(order, django_capture_on_commit_callbacks):
    Order.objects.filter(pk=order.pk).update(tracking_number="TRK-998877")
    order.refresh_from_db()
    for s in ("confirmed", "processing"):
        change_status(order, s)
    run(order, "shipped", django_capture_on_commit_callbacks)
    body = mail.outbox[0].body
    assert "TRK-998877" in body and "cash" in body.lower() and f"{int(order.total):,}" in body
    assert "TRK-998877" in mail.outbox[0].alternatives[0][0]


def test_delivered_email_confirms_cash_payment(order, django_capture_on_commit_callbacks):
    for s in ("confirmed", "processing", "shipped"):
        change_status(order, s)
    run(order, "delivered", django_capture_on_commit_callbacks)
    assert "cash payment" in mail.outbox[0].body


def test_cancel_by_store_vs_customer_wording(order, user, django_capture_on_commit_callbacks):
    run(order, "cancelled", django_capture_on_commit_callbacks, by=user)
    assert "As you asked" in mail.outbox[0].body and "charged anything" in mail.outbox[0].body
    other = create_order(user=user, lines=[(Product.objects.get(slug="retro-everyday-sunglasses").pk, 0, 1)], data=DELIVERY)
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    run(other, "cancelled", django_capture_on_commit_callbacks, by=staff)
    assert "we had to cancel" in mail.outbox[0].body


def test_no_email_for_invalid_or_repeat_transition(order, django_capture_on_commit_callbacks):
    from apps.orders.services import OrderError

    run(order, "confirmed", django_capture_on_commit_callbacks)
    run(order, "confirmed", django_capture_on_commit_callbacks)  # same status: no-op
    assert len(mail.outbox) == 0
    with pytest.raises(OrderError):
        run(order, "delivered", django_capture_on_commit_callbacks)  # illegal jump
    assert len(mail.outbox) == 0


def test_email_failure_never_blocks_the_status_change(order, settings, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST, settings.EMAIL_PORT = "127.0.0.1", 1
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, "confirmed")
    order.refresh_from_db()
    assert order.status == "confirmed"


def test_admin_bulk_action_sends_emails(client, order, django_capture_on_commit_callbacks):
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    s = client.session; s["2fa_verified"] = True; s.save()
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("admin:orders_order_changelist"), {"action": "mark_confirmed", "_selected_action": [order.pk]})
    assert len(mail.outbox) == 1 and "is confirmed" in mail.outbox[0].subject


def test_emails_use_site_url_for_links(order, settings, django_capture_on_commit_callbacks):
    settings.SITE_URL = "https://shop.example.com/"
    run(order, "confirmed", django_capture_on_commit_callbacks)
    assert f"https://shop.example.com/orders/{order.number}/" in mail.outbox[0].body


# ======================= health check =======================
def test_healthz_ok_and_uncached(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and "no-store" in r["Cache-Control"]


def test_healthz_reports_release_label_and_exact_version(client):
    with override_settings(APP_RELEASE="v1.2.0", APP_VERSION="6476069f18fd"):
        body = client.get("/healthz").json()
    assert body["release"] == "v1.2.0" and body["version"] == "6476069f18fd"


def test_admin_footer_shows_the_release(client, settings):
    from django.conf import settings as s

    assert s.APP_RELEASE in s.JAZZMIN_SETTINGS["copyright"]


def test_healthz_reports_503_when_database_is_down(client, monkeypatch):
    from django.db.backends.utils import CursorWrapper

    def boom(self, *a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(CursorWrapper, "execute", boom)
    assert client.get("/healthz").status_code == 503


def test_vendored_static_files_do_not_reference_missing_source_maps():
    """Production static storage fails collectstatic on a sourceMappingURL whose .map file isn't shipped."""
    from pathlib import Path

    offenders = [str(p) for p in Path("static").rglob("*") if p.suffix in (".js", ".css") and "sourceMappingURL" in p.read_text(errors="ignore")]
    assert not offenders, offenders


def test_collectstatic_succeeds_with_the_production_storage(tmp_path, settings):
    from django.core.management import call_command

    settings.STATIC_ROOT = str(tmp_path)
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
    call_command("collectstatic", interactive=False, verbosity=0)
    assert list(tmp_path.glob("css/app.*.css")) and (tmp_path / "staticfiles.json").exists()


# ======================= configurable admin path =======================
def _admin_probe(admin_url: str | None):
    """Start Django in a clean process with ADMIN_URL set and report where the admin index lives."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.development"}
    env.pop("ADMIN_URL", None)
    if admin_url is not None:
        env["ADMIN_URL"] = admin_url
    code = "import django; django.setup(); from django.urls import reverse; print(reverse('admin:index'))"
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)


def test_admin_defaults_to_admin():
    assert _admin_probe(None).stdout.strip() == "/admin/"


def test_admin_url_can_be_moved_and_is_normalised():
    for raw in ("manage-k7x2p9/", "/manage-k7x2p9", " manage-k7x2p9 ", "//manage-k7x2p9//"):
        assert _admin_probe(raw).stdout.strip() == "/manage-k7x2p9/", raw
    assert _admin_probe("staff/panel").stdout.strip() == "/staff/panel/"


def test_unsafe_admin_urls_are_rejected():
    for bad in ("a b/", "../x/", "adm?in", "a//b", "%2e/"):
        result = _admin_probe(bad)
        assert result.returncode != 0 and "ADMIN_URL" in result.stderr, bad


def test_default_admin_path_serves_the_admin_and_login_redirect_follows_it(client):
    assert reverse("admin:index").startswith("/")
    assert client.get(reverse("admin:login")).status_code == 200


# ======================= production static storage x admin (regression for the post-login 500) =======================
@pytest.fixture
def prod_static(settings, tmp_path):
    from django.core.management import call_command

    settings.STATIC_ROOT = str(tmp_path / "static")
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "config.storage.DirectoryTolerantManifestStorage"},
    }
    call_command("collectstatic", interactive=False, verbosity=0)
    return settings


def test_admin_pages_render_with_the_production_static_storage(client, prod_static):
    """Jazzmin's layout references the folder 'vendor/bootswatch'; strict manifest storage turned that into a 500."""
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    for name in ("admin:index", "admin:catalog_product_changelist", "admin:orders_order_changelist", "admin:accounts_user_changelist",
                 "admin:core_page_changelist", "admin:catalog_product_import"):
        resp = client.get(reverse(name))
        assert resp.status_code == 200, (name, resp.status_code)
    assert b"/static/css/admin." in client.get(reverse("admin:index")).content  # fingerprinted, i.e. the manifest is really in use


def test_folder_references_are_tolerated_but_missing_files_still_fail(prod_static):
    from django.contrib.staticfiles.storage import staticfiles_storage

    assert staticfiles_storage.url("vendor/bootswatch") == "/static/vendor/bootswatch"
    assert ".css" in staticfiles_storage.url("css/app.css") and staticfiles_storage.url("css/app.css") != "/static/css/app.css"
    with pytest.raises(ValueError):
        staticfiles_storage.url("css/does-not-exist.css")


def test_errors_are_logged_to_the_console_with_tracebacks():
    from django.conf import settings as s

    loggers = s.LOGGING["loggers"]
    assert loggers["django.request"]["level"] == "ERROR" and "console" in loggers["django.request"]["handlers"]
    assert s.LOGGING["handlers"]["console"]["class"] == "logging.StreamHandler"
