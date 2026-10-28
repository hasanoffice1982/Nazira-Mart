import io
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.twofactor import VERIFIED_KEY
from apps.blog import services
from apps.blog.models import Category, Post, Tag

pytestmark = pytest.mark.django_db
XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def png(size=(40, 30)) -> SimpleUploadedFile:
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "PNG")
    return SimpleUploadedFile("pic.png", buf.getvalue(), "image/png")


def post(**kw) -> Post:
    kw.setdefault("title", "Bag care tips")
    kw.setdefault("body", "<p>Hello world</p>")
    kw.setdefault("is_published", True)
    return Post.objects.create(**kw)


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_superuser("boss@example.com", "pw-12345-xyz")
    client.force_login(user)
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    return user


def test_sanitizer_drops_foreign_images_and_scripts_but_keeps_own_images_and_tables():
    html = (
        '<p>x</p><script>alert(1)</script><img src="https://cdn.rankyak.com/a.png" alt="a">'
        '<img src="/media/blog/inline/a.webp" alt="b"><table><tr><td>1</td></tr></table>'
    )
    out = services.clean_article_html(html)
    assert "<script" not in out and "rankyak" not in out
    assert "/media/blog/inline/a.webp" in out and "<table>" in out
    assert services.foreign_images(html) == ["https://cdn.rankyak.com/a.png"]


def test_author_is_always_the_shop():
    assert post().author_name == settings.SITE_NAME


def test_slug_is_generated_and_unique():
    a, b = post(), post()
    assert a.slug and a.slug != b.slug


def test_drafts_and_scheduled_posts_are_hidden(client):
    draft = post(title="Draft", is_published=False)
    future = post(title="Future", published_at=timezone.now() + timedelta(days=2))
    live = post(title="Live")
    assert client.get(draft.get_absolute_url()).status_code == 404
    assert client.get(future.get_absolute_url()).status_code == 404
    assert client.get(live.get_absolute_url()).status_code == 200
    page = client.get(reverse("blog:index")).content.decode()
    assert "Live" in page and "Draft" not in page and "Future" not in page


def test_category_and_tag_filters(client):
    cat, tag = Category.objects.create(name="Bags"), Tag.objects.create(name="care")
    inside = post(title="Inside", category=cat)
    inside.tags.add(tag)
    post(title="Outside")
    for url in (reverse("blog:category", args=[cat.slug]), reverse("blog:tag", args=[tag.slug])):
        html = client.get(url).content.decode()
        assert "Inside" in html and "Outside" not in html
    assert client.get(reverse("blog:category", args=["nope"])).status_code == 404


def test_detail_has_article_json_ld_and_is_in_sitemap(client):
    p = post()
    html = client.get(p.get_absolute_url()).content.decode()
    assert "BlogPosting" in html
    assert p.get_absolute_url() in client.get("/sitemap.xml").content.decode()


def test_upload_endpoint_needs_staff_and_xhr_header(client, staff):
    url = reverse("admin:blog_post_upload_image")
    assert client.post(url, {"file": png()}).status_code == 400
    res = client.post(url, {"file": png()}, **XHR)
    assert res.status_code == 200 and res.json()["location"].endswith(".webp")
    assert client.post(url, {"file": SimpleUploadedFile("x.txt", b"nope", "text/plain")}, **XHR).status_code == 400


def test_upload_endpoint_rejects_anonymous(client):
    res = client.post(reverse("admin:blog_post_upload_image"), {"file": png()}, **XHR)
    assert res.status_code in (302, 403)
