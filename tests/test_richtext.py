import pytest
from django.urls import reverse

from apps.accounts.twofactor import VERIFIED_KEY
from apps.catalog.models import Category, Product
from apps.core.models import Page, SiteSettings
from apps.core.richtext import rich

pytestmark = pytest.mark.django_db


def test_scripts_and_handlers_are_stripped():
    html = str(rich('<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a><img src=x onerror=y><a href="https://ok.com">ok</a>'))
    assert "<script" not in html and "onclick" not in html and "javascript:" not in html and "<img" not in html
    assert 'href="https://ok.com"' in html and "noopener" in html


def test_legacy_plain_text_still_renders():
    html = str(rich("## Title\n\n- one\n- two\n\nSome **bold** and a < b"))
    assert "<h2>Title</h2>" in html and "<li>one</li>" in html and "<strong>bold</strong>" in html and "<script" not in html


def test_page_renders_rich_body_with_tokens(client):
    Page.objects.create(slug="faq", title="FAQ", body="<h2>Hello</h2><p>Free over [[currency]][[free_delivery]]</p><script>x</script>")
    html = client.get(reverse("core:page", args=["faq"])).content.decode()
    assert "<h2>Hello</h2>" in html and "<script>x" not in html and "৳5,000" in html


def test_models_sanitise_on_save():
    cat = Category.objects.create(name="C", slug="c", title="C")
    p = Product.objects.create(category=cat, name="P", slug="p", sku="P1", price=10, art_type="a", description="<p>ok</p><script>bad()</script>")
    assert "script" not in p.description and "<p>ok</p>" in p.description
    s = SiteSettings.load_fresh()
    s.story_text = '<p onmouseover="x">Story</p>'
    s.save()
    assert "onmouseover" not in SiteSettings.load_fresh().story_text


def test_product_page_shows_description_html_and_meta_is_plain(client):
    cat = Category.objects.create(name="C", slug="c", title="C")
    p = Product.objects.create(category=cat, name="P", slug="p", sku="P1", price=10, art_type="a", description="<p>Soft <strong>cotton</strong></p>")
    html = client.get(p.get_absolute_url()).content.decode()
    assert "<strong>cotton</strong>" in html and 'content="Soft cotton"' in html


def test_admin_uses_editor_for_big_text(admin_client):
    session = admin_client.session
    session[VERIFIED_KEY] = True
    session.save()
    page = Page.objects.create(slug="t", title="T", body="<p>x</p>")
    r = admin_client.get(reverse("admin:core_page_change", args=[page.pk]))
    assert r.status_code == 200 and b"tinymce" in r.content.lower()


def test_uploaded_photo_is_shrunk(tmp_path, settings):
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    settings.MEDIA_ROOT = str(tmp_path)
    buf = BytesIO()
    Image.effect_noise((3000, 2000), 80).convert("RGB").save(buf, "JPEG", quality=95)
    big = len(buf.getvalue())
    cat = Category.objects.create(name="C", slug="c", title="C", image=SimpleUploadedFile("huge.jpg", buf.getvalue(), "image/jpeg"))
    with cat.image.open("rb") as fh, Image.open(fh) as img:
        assert img.format == "WEBP" and max(img.size) <= 1600
    assert cat.image.name.endswith(".webp") and cat.image.size < big / 3


def test_hero_promo_and_footer_text_are_rich_and_sanitised(client):
    from apps.core.models import HeroSlide, PromoBanner

    HeroSlide.objects.create(title="Hero", text="<p>Big <strong>sale</strong></p><script>x()</script>")
    PromoBanner.objects.create(title="Promo", text="<p>Promo <em>text</em></p>")
    s = SiteSettings.load_fresh()
    s.footer_about = "<p>About <a href='https://e.com' onclick='x()'>us</a></p>"
    s.save()
    html = client.get("/").content.decode()
    assert "<strong>sale</strong>" in html and "<em>text</em>" in html and "<script>x" not in html and "onclick" not in html
    assert 'href="https://e.com"' in html
