import json
import re

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Product
from apps.core.models import SiteSettings

pytestmark = pytest.mark.django_db
SITE = "https://narizmart.com"


@pytest.fixture(autouse=True)
def shop(settings):
    settings.SITE_URL = SITE
    call_command("seed_catalog", dataset="test")


def head(client, url):
    html = client.get(url).content.decode()
    return html


def tag(html, pattern):
    m = re.search(pattern, html)
    return m.group(1) if m else None


def graph(html):
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    return json.loads(raw)["@graph"]


def test_home_has_description_canonical_open_graph_twitter_and_org_json_ld(client):
    html = head(client, "/")
    assert tag(html, r'<link rel="canonical" href="([^"]+)"') == SITE + "/"
    assert tag(html, r'<meta property="og:type" content="([^"]+)"') == "website"
    assert tag(html, r'<meta property="og:image" content="([^"]+)"').startswith(SITE + "/static/img/og-default")
    assert 'name="twitter:card" content="summary_large_image"' in html and 'name="description"' in html
    assert "index,follow" in html
    types = [n["@type"] for n in graph(html)]
    assert "Organization" in types and "WebSite" in types and "BreadcrumbList" not in types
    site = next(n for n in graph(html) if n["@type"] == "WebSite")
    assert "search_term_string" in json.dumps(site["potentialAction"])


def test_organization_uses_the_real_contact_details(client):
    SiteSettings.load_fresh()
    SiteSettings.objects.filter(pk=1).update(contact_phone="+880 1765-525624", contact_email="hello@narizmart.com", facebook_url="https://facebook.com/narizmart")
    from django.core.cache import cache

    cache.clear()
    org = next(n for n in graph(head(client, "/")) if n["@type"] == "Organization")
    assert org["telephone"] == "+880 1765-525624" and org["email"] == "hello@narizmart.com" and org["sameAs"] == ["https://facebook.com/narizmart"]
    assert org["address"]["addressCountry"] == "BD"


def test_product_page_has_product_json_ld_with_price_stock_breadcrumbs_and_no_fake_rating(client):
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    html = head(client, p.get_absolute_url())
    nodes = {n["@type"]: n for n in graph(html)}
    prod = nodes["Product"]
    assert prod["name"] == p.name and prod["sku"] == p.sku and prod["offers"]["priceCurrency"] == "BDT"
    assert prod["offers"]["price"] == "690.00" and prod["offers"]["availability"].endswith("InStock")
    assert "aggregateRating" not in prod and "review" not in prod
    assert [i["name"] for i in nodes["BreadcrumbList"]["itemListElement"]] == ["Home", "Shop", p.category.name, p.name]
    assert tag(html, r'<meta property="og:type" content="([^"]+)"') == "product"
    assert tag(html, r'<meta property="product:price:amount" content="([^"]+)"') == "690.00"
    assert tag(html, r'<meta property="og:title" content="([^"]+)"').startswith(p.name)


def test_out_of_stock_product_says_so(client):
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    Product.objects.filter(pk=p.pk).update(stock=0)
    prod = next(n for n in graph(head(client, p.get_absolute_url())) if n["@type"] == "Product")
    assert prod["offers"]["availability"].endswith("OutOfStock")


def test_product_with_variants_uses_a_price_range(client):
    p = Product.objects.get(slug="everyday-classic-tote-bag")   # sand, charcoal and a 1390 sage variant
    offers = next(n for n in graph(head(client, p.get_absolute_url())) if n["@type"] == "Product")["offers"]
    assert offers["@type"] == "AggregateOffer" and float(offers["lowPrice"]) <= float(offers["highPrice"])


def test_script_closing_tags_in_data_cannot_break_the_page(client):
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    Product.objects.filter(pk=p.pk).update(name='Evil </script><script>alert(1)</script> & co')
    html = head(client, p.get_absolute_url())
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
    assert "</script" not in block and "<" not in block and "\\u003c" in block
    assert "alert(1)</script>" not in html


def test_canonical_drops_sort_and_search_but_keeps_the_category(client):
    assert tag(head(client, "/shop/?category=toys&sort=new&utm_source=fb"), r'<link rel="canonical" href="([^"]+)"') == SITE + "/shop/?category=toys"


def test_category_page_has_its_own_title_and_breadcrumbs(client):
    html = head(client, "/shop/?category=fashion")
    assert "<title>Fashion Finds — NarizMart</title>" in html
    crumbs = next(n for n in graph(html) if n["@type"] == "BreadcrumbList")
    assert [i["name"] for i in crumbs["itemListElement"]] == ["Home", "Shop", "Fashion"]
    assert next(n for n in graph(html) if n["@id"].endswith("#webpage"))["@type"] == "CollectionPage"


def test_search_results_unknown_categories_and_private_pages_are_noindex(client):
    assert 'content="noindex,follow"' in head(client, "/shop/?q=bag")
    assert 'content="noindex,follow"' in head(client, "/shop/?category=does-not-exist")
    assert 'content="noindex,follow"' in head(client, reverse("accounts:login"))
    assert 'content="noindex,follow"' not in head(client, "/p/help-center/")


def test_static_page_uses_its_own_description_and_breadcrumb(client):
    html = head(client, "/p/shipping-delivery/")
    assert "Delivery areas, charges" in tag(html, r'<meta name="description" content="([^"]+)"')
    assert [i["name"] for i in next(n for n in graph(html) if n["@type"] == "BreadcrumbList")["itemListElement"]] == ["Home", "Shipping & Delivery"]


def test_description_is_clean_text_and_short(client):
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    Product.objects.filter(pk=p.pk).update(description="<p>" + "Soft <b>bold</b> words. " * 60 + "</p>")
    desc = tag(head(client, p.get_absolute_url()), r'<meta name="description" content="([^"]+)"')
    assert "<" not in desc and len(desc) <= 160 and desc.endswith("…")


def test_product_photo_becomes_the_share_image(client, tmp_path, settings):
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image

    settings.MEDIA_ROOT = str(tmp_path)
    p = Product.objects.get(slug="retro-everyday-sunglasses")
    buf = BytesIO()
    Image.new("RGB", (1300, 900), "teal").save(buf, "PNG")
    p.image.save("x.png", ContentFile(buf.getvalue()), save=True)
    html = head(client, p.get_absolute_url())
    og = tag(html, r'<meta property="og:image" content="([^"]+)"')
    assert og.startswith(SITE) and "renditions" in og and og.endswith(".webp")
    assert og in next(n for n in graph(html) if n["@type"] == "Product")["image"]
