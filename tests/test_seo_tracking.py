import json
import xml.etree.ElementTree as ET

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.admin import SiteSettingsForm
from apps.core.models import Page, SiteSettings
from apps.orders.services import create_order

User = get_user_model()
pytestmark = pytest.mark.django_db
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
DELIVERY = {"full_name": "Rafi", "phone": "01712345678", "address": "House 1", "city": "Dhaka", "delivery_area": "dhaka"}


@pytest.fixture(autouse=True)
def seeded(settings):
    cache.clear()
    settings.SITE_URL = "https://narizmart.com"
    call_command("seed_catalog", dataset="test")


def site(**kw):
    s = SiteSettings.load_fresh()
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()


# ---- sitemap / robots ---------------------------------------------------------------------------------------------
def test_sitemap_lists_public_pages_products_categories_with_canonical_domain(client):
    Page.objects.create(slug="faq", title="FAQ", body="x")
    Page.objects.create(slug="hidden", title="Hidden", body="x", is_published=False)
    inactive = Product.objects.get(slug="smart-active-watch")
    inactive.is_active = False
    inactive.save()
    cache.clear()
    resp = client.get("/sitemap.xml", HTTP_HOST="www.narizmart.com" if False else "testserver")
    assert resp.status_code == 200 and resp["Content-Type"].startswith("application/xml")
    locs = [e.text for e in ET.fromstring(resp.content).findall("s:url/s:loc", NS)]
    assert all(l.startswith("https://narizmart.com/") for l in locs)
    assert "https://narizmart.com/" in locs and "https://narizmart.com/shop/" in locs
    assert "https://narizmart.com/product/studio-wireless-headphones/" in locs
    assert "https://narizmart.com/product/smart-active-watch/" not in locs  # inactive
    assert "https://narizmart.com/p/faq/" in locs and not any("hidden" in l for l in locs)
    assert any("category=tech" in l for l in locs)
    assert not any(x in l for l in locs for x in ("/cart", "/orders", "/accounts", "/wishlist", "/checkout"))


def test_robots_points_to_sitemap_and_hides_private_areas(client, settings):
    body = client.get("/robots.txt").content.decode()
    assert "Sitemap: https://narizmart.com/sitemap.xml" in body
    for path in ("/cart/", "/orders/", "/accounts/", "/wishlist/"):
        assert f"Disallow: {path}" in body
    assert settings.ADMIN_URL.strip("/") not in body  # never advertise the admin path


# ---- tracking codes in the admin ----------------------------------------------------------------------------------
def form(**data):
    base = {f.name: getattr(SiteSettings.load_fresh(), f.name) for f in SiteSettings._meta.fields if f.name not in ("id",)}
    return SiteSettingsForm(data={**{k: ("" if v is None else v) for k, v in base.items() if not hasattr(v, "name")}, **data}, instance=SiteSettings.load_fresh())


@pytest.mark.parametrize("pasted,expected", [
    ("123456789012345", "123456789012345"),
    ("<script>fbq('init', '987654321098765'); fbq('track', 'PageView');</script>", "987654321098765"),
])
def test_meta_pixel_accepts_id_or_whole_snippet(pasted, expected):
    f = form(meta_pixel_id=pasted)
    f.is_valid()
    assert f.cleaned_data["meta_pixel_id"] == expected


@pytest.mark.parametrize("pasted,expected", [
    ("G-ABC123XYZ9", "G-ABC123XYZ9"),
    ("gtm-abcd123", "GTM-ABCD123"),
    ("<script async src='https://www.googletagmanager.com/gtag/js?id=G-TEST12345'></script> gtag('config','G-TEST12345')", "G-TEST12345"),
    ("AW-123456789", "AW-123456789"),
])
def test_google_tag_accepts_id_or_whole_snippet(pasted, expected):
    f = form(google_tag_id=pasted)
    f.is_valid()
    assert f.cleaned_data["google_tag_id"] == expected


def test_garbage_is_rejected_not_injected():
    f = form(meta_pixel_id="<script>alert(1)</script>", google_tag_id="alert(1)")
    assert not f.is_valid() and "meta_pixel_id" in f.errors and "google_tag_id" in f.errors


def test_model_rejects_bad_ids():
    s = SiteSettings.load_fresh()
    s.meta_pixel_id, s.google_tag_id = "12", "nope"
    with pytest.raises(ValidationError) as e:
        s.full_clean()
    assert "meta_pixel_id" in e.value.message_dict and "google_tag_id" in e.value.message_dict


# ---- rendering ----------------------------------------------------------------------------------------------------
def test_nothing_is_loaded_until_a_code_is_set(client):
    resp = client.get("/")
    html = resp.content.decode()
    assert "bm-tracking" not in html and "tracking.js" not in html
    assert "facebook" not in resp["Content-Security-Policy"] and "googletagmanager" not in resp["Content-Security-Policy"]


def test_codes_render_and_csp_allows_only_those_vendors(client):
    site(meta_pixel_id="123456789012345", google_tag_id="G-ABC123XYZ9", google_site_verification="abcdefghij1234", meta_domain_verification="zyxwvutsrq9876")
    resp = client.get("/")
    html, csp = resp.content.decode(), resp["Content-Security-Policy"]
    assert 'data-meta="123456789012345"' in html and 'data-google="G-ABC123XYZ9"' in html and "js/tracking" in html
    assert 'name="google-site-verification" content="abcdefghij1234"' in html and 'name="facebook-domain-verification"' in html
    assert "https://connect.facebook.net" in csp and "https://www.googletagmanager.com" in csp
    site(meta_pixel_id="")
    csp2 = client.get("/").headers["Content-Security-Policy"]
    assert "facebook" not in csp2 and "googletagmanager" in csp2


def test_admin_pages_keep_their_own_csp(admin_client, settings):
    from apps.accounts.twofactor import VERIFIED_KEY

    s = admin_client.session
    s[VERIFIED_KEY] = True
    s.save()
    site(meta_pixel_id="123456789012345")
    csp = admin_client.get(reverse("admin:core_sitesettings_changelist"), follow=True).headers["Content-Security-Policy"]
    assert "facebook" not in csp


# ---- events -------------------------------------------------------------------------------------------------------
def payload(html):
    marker = 'id="bm-track-data" type="application/json">'
    return json.loads(html.split(marker)[1].split("</script>")[0])


def test_product_page_sends_view_item(client):
    site(meta_pixel_id="123456789012345")
    p = Product.objects.get(slug="studio-wireless-headphones")
    data = payload(client.get(p.get_absolute_url()).content.decode())
    assert data["event"] == "view_item" and data["currency"] == "BDT" and data["items"][0]["id"] == str(p.pk) and data["value"] == 2490


def test_events_can_be_switched_off(client):
    site(meta_pixel_id="123456789012345", track_shopping_events=False)
    p = Product.objects.get(slug="studio-wireless-headphones")
    html = client.get(p.get_absolute_url()).content.decode()
    assert 'data-events="0"' in html and "bm-track-data" not in html


def test_add_to_cart_announces_event_in_hx_trigger(client):
    site(meta_pixel_id="123456789012345")
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    resp = client.post(reverse("cart:add", args=[p.pk]))
    trig = json.loads(resp["HX-Trigger"])
    assert trig["track"]["event"] == "add_to_cart" and trig["track"]["items"][0]["name"] == p.name and "toast" in trig


def test_checkout_and_purchase_events(client):
    site(meta_pixel_id="123456789012345", currency_code="usd")
    assert SiteSettings.load_fresh().currency_code == "USD"
    buyer = User.objects.create_user("buyer@example.com", "pass12345!x", email_verified=True)
    client.force_login(buyer)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    client.post(reverse("cart:add", args=[p.pk]))
    checkout = payload(client.get(reverse("orders:checkout")).content.decode())
    assert checkout["event"] == "begin_checkout" and checkout["currency"] == "USD" and checkout["value"] == 690
    order = create_order(user=buyer, lines=[(p.pk, 0, 2)], data=DELIVERY)
    done = payload(client.get(reverse("orders:success", args=[order.number])).content.decode())
    assert done["event"] == "purchase" and done["transaction_id"] == order.number and done["value"] == float(order.total)


def test_tracking_script_is_valid_and_has_no_hardcoded_ids():
    js = open("static/js/tracking.js").read()
    assert "dataset.meta" in js and "dataset.google" in js
    import re

    assert not re.search(r"G-[A-Z0-9]{6,}|\b\d{15}\b", js)


@pytest.mark.parametrize("pasted,expected", [
    ('<meta name="google-site-verification" content="abcDEF123_-xyz7890" />', "abcDEF123_-xyz7890"),
    ("abcDEF123_-xyz7890", "abcDEF123_-xyz7890"),
])
def test_verification_accepts_full_meta_tag_or_code(pasted, expected):
    f = form(google_site_verification=pasted, meta_domain_verification=pasted.replace("google-site-verification", "facebook-domain-verification"))
    f.is_valid()
    assert f.cleaned_data["google_site_verification"] == expected and f.cleaned_data["meta_domain_verification"] == expected


def test_bad_verification_text_is_rejected():
    f = form(google_site_verification="<script>alert(1)</script>")
    assert not f.is_valid() and "google_site_verification" in f.errors
