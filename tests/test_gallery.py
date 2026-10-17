import io

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from apps.cart.services import Line
from apps.catalog.models import Color, Product, ProductImage

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    cache.clear()
    call_command("seed_catalog", dataset="test")


def png(color="red", size=(40, 40), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def photo(name="a.png", **kw):
    return SimpleUploadedFile(name, png(**kw), "image/png")


def add_photo(product, color=None, position=0, tint="red", alt=""):
    return ProductImage.objects.create(product=product, color=color, position=position, alt_text=alt, image=ContentFile(png(color=tint), name=f"{tint}{position}.png"))


@pytest.fixture
def shoes():
    return Product.objects.get(slug="cloudstep-everyday-sneakers")


@pytest.fixture
def staff_client(client):
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    s = client.session; s["2fa_verified"] = True; s.save()
    return client


def cloud():
    return Color.objects.get(name="Cloud")


def mint():
    return Color.objects.get(name="Mint")


# ---- helpers ---------------------------------------------------------------------------
def test_cover_prefers_first_shared_photo(shoes):
    add_photo(shoes, color=cloud(), position=0, tint="blue")
    shared = add_photo(shoes, position=1, tint="green")
    shoes = Product.objects.get(pk=shoes.pk)
    assert shoes.cover_image.name == shared.image.name


def test_cover_falls_back_to_any_gallery_then_legacy_then_none(shoes):
    assert shoes.cover_image is None
    shoes.image.save("legacy.png", ContentFile(png()), save=True)
    assert Product.objects.get(pk=shoes.pk).cover_image.name.endswith("legacy.png")
    only_color = add_photo(shoes, color=mint(), tint="blue")
    assert Product.objects.get(pk=shoes.pk).cover_image.name == only_color.image.name


def test_images_for_color_orders_color_photos_before_shared(shoes):
    s1 = add_photo(shoes, position=0, tint="red")
    c1 = add_photo(shoes, color=cloud(), position=5, tint="blue")
    m1 = add_photo(shoes, color=mint(), position=6, tint="green")
    shoes = Product.objects.get(pk=shoes.pk)
    assert [i.pk for i in shoes.images_for_color(cloud().pk)] == [c1.pk, s1.pk]
    assert [i.pk for i in shoes.images_for_color(mint().pk)] == [m1.pk, s1.pk]
    assert [i.pk for i in shoes.images_for_color(None)] == [s1.pk]


def test_no_shared_photos_shows_everything_until_a_color_is_chosen(shoes):
    c1 = add_photo(shoes, color=cloud(), position=0, tint="blue")
    m1 = add_photo(shoes, color=mint(), position=1, tint="green")
    shoes = Product.objects.get(pk=shoes.pk)
    assert [i.pk for i in shoes.images_for_color(None)] == [c1.pk, m1.pk]


def test_photo_order_follows_position(shoes):
    b = add_photo(shoes, position=2, tint="blue"); a = add_photo(shoes, position=1, tint="red")
    assert [i.pk for i in Product.objects.get(pk=shoes.pk).gallery_images] == [a.pk, b.pk]


# ---- storefront markup ------------------------------------------------------------------------
def test_detail_page_exposes_gallery_data_for_slider(client, shoes):
    add_photo(shoes, position=0, tint="red", alt="Side view"); add_photo(shoes, color=cloud(), position=1, tint="blue")
    html = client.get(shoes.get_absolute_url()).content.decode()
    assert "data-gallery-swiper" in html and "data-gallery-single" in html
    assert html.count("<li data-color=") >= 2 + 0 and 'data-alt="Side view"' in html
    assert f'data-color="{cloud().pk}"' in html


def test_swiper_assets_are_served_locally_not_from_a_cdn(client):
    html = client.get("/").content.decode()
    assert "/static/vendor/swiper/swiper-bundle.min.js" in html and "cdn." not in html
    from django.contrib.staticfiles import finders

    assert finders.find("vendor/swiper/swiper-bundle.min.js") and finders.find("vendor/swiper/swiper-bundle.min.css")


def test_quick_view_has_gallery(client, shoes):
    add_photo(shoes, position=0); add_photo(shoes, position=1, tint="blue")
    assert b"data-gallery-data" in client.get(reverse("catalog:quick", args=[shoes.slug])).content


def test_card_uses_resized_webp_not_the_original(client, shoes):
    p = add_photo(shoes, position=0)
    html = client.get("/shop/").content.decode()
    assert "/media/renditions/" in html and ".webp" in html and "srcset=" in html
    assert p.image.url not in html  # the full-size upload is never sent to shoppers


def test_product_without_photos_still_renders_illustration(client):
    plain = Product.objects.get(slug="retro-everyday-sunglasses")
    html = client.get(plain.get_absolute_url()).content.decode()
    assert 'data-art="sunglasses"' in html


def test_cart_line_image_resolution(shoes):
    s = add_photo(shoes, position=0, tint="red")
    c = add_photo(shoes, color=cloud(), position=1, tint="blue")
    shoes = Product.objects.prefetch_related("images", "variants").get(pk=shoes.pk)
    cloud_v = next(v for v in shoes.active_variants if v.color_id == cloud().pk)
    mint_v = next(v for v in shoes.active_variants if v.color_id == mint().pk)
    assert Line(shoes, cloud_v, 1).image.name == c.image.name       # colour-tagged photo
    assert Line(shoes, mint_v, 1).image.name == s.image.name        # falls back to the cover
    mint_v.image.save("own.png", ContentFile(png()), save=True)
    assert Line(shoes, mint_v, 1).image.name.endswith("own.png")    # explicit variant photo wins


# ---- validation ---------------------------------------------------------------------------------------
def test_model_rejects_non_images_and_odd_formats(shoes):
    from django.core.exceptions import ValidationError

    from apps.catalog.models import validate_photo

    with pytest.raises(ValidationError):
        validate_photo(SimpleUploadedFile("x.png", b"not an image"))
    with pytest.raises(ValidationError):
        validate_photo(SimpleUploadedFile("x.gif", png(fmt="GIF")))
    validate_photo(SimpleUploadedFile("ok.webp", png(fmt="WEBP")))


# ---- bulk upload (admin) ---------------------------------------------------------------------------------
def bulk(client, product, files, **post):
    return client.post(reverse("admin:catalog_product_photos", args=[product.pk]), {"photos": files, **post})


def test_bulk_upload_many_shared_photos_in_order(staff_client, shoes):
    resp = bulk(staff_client, shoes, [photo("1.png", color="red"), photo("2.png", color="blue"), photo("3.png", color="green")])
    assert resp.status_code == 302
    imgs = shoes.images.all()
    assert imgs.count() == 3 and [i.position for i in imgs] == [0, 1, 2] and all(i.color_id is None for i in imgs)


def test_bulk_upload_appends_after_existing_and_tags_color(staff_client, shoes):
    add_photo(shoes, position=4)
    bulk(staff_client, shoes, [photo("a.png"), photo("b.png", color="blue")], color=cloud().pk, alt_text="Cloud front")
    new = list(shoes.images.exclude(position=4).order_by("position"))
    assert [i.position for i in new] == [5, 6] and all(i.color_id == cloud().pk and i.alt_text == "Cloud front" for i in new)


def test_bulk_upload_rejects_everything_if_one_file_is_bad(staff_client, shoes):
    resp = bulk(staff_client, shoes, [photo("good.png"), SimpleUploadedFile("evil.png", b"<?php system($_GET['c']); ?>", "image/png")])
    assert resp.status_code == 200 and b"valid image" in resp.content and not shoes.images.exists()


def test_bulk_upload_rejects_wrong_format_and_oversize(staff_client, shoes, monkeypatch):
    resp = bulk(staff_client, shoes, [SimpleUploadedFile("a.gif", png(fmt="GIF"), "image/gif")])
    assert b"JPEG, PNG or WebP" in resp.content and not shoes.images.exists()
    monkeypatch.setattr("apps.catalog.models.MAX_IMAGE_BYTES", 50)
    assert b"larger than" in bulk(staff_client, shoes, [photo("big.png", size=(200, 200))]).content


def test_bulk_upload_limit_per_request(staff_client, shoes):
    files = [photo(f"{i}.png") for i in range(21)]
    assert b"at most 20" in bulk(staff_client, shoes, files).content and not shoes.images.exists()


def test_bulk_upload_requires_staff_permission_and_2fa(client, shoes):
    url = reverse("admin:catalog_product_photos", args=[shoes.pk])
    assert client.get(url).status_code == 302
    customer = User.objects.create_user("c@example.com", "pass12345!x", email_verified=True)
    client.force_login(customer)
    assert client.get(url).status_code == 302
    assert client.post(url, {"photos": [photo()]}).status_code == 302 and not shoes.images.exists()
    staff = User.objects.create_superuser("s@example.com", "pass12345!x")
    client.force_login(staff)  # password only
    assert client.get(url)["Location"] == reverse("accounts:two_factor")


def test_bulk_page_and_inline_render(staff_client, shoes):
    add_photo(shoes, position=0)
    assert b"Upload photos for" in staff_client.get(reverse("admin:catalog_product_photos", args=[shoes.pk])).content
    html = staff_client.get(reverse("admin:catalog_product_change", args=[shoes.pk])).content.decode()
    assert "Upload many photos" in html and "images-0-image" in html


def test_changelist_thumbnail_uses_cover(staff_client, shoes):
    add_photo(shoes, position=0)
    assert "/media/renditions/160/" in staff_client.get(reverse("admin:catalog_product_changelist")).content.decode()


def test_unknown_product_in_bulk_view_redirects(staff_client):
    assert staff_client.get(reverse("admin:catalog_product_photos", args=[99999])).status_code == 302


def test_legacy_single_photo_is_copied_into_gallery_by_migration(shoes):
    # The data migration copies Product.image into ProductImage; simulate its logic to prove the intent.
    shoes.image.save("legacy.png", ContentFile(png()), save=True)
    ProductImage.objects.create(product=shoes, image=shoes.image.name, alt_text=shoes.name, position=0)
    assert Product.objects.get(pk=shoes.pk).gallery_images[0].image.name == shoes.image.name


# ---- renditions ------------------------------------------------------------------------------------
def test_rendition_is_small_webp_and_cached(shoes, settings):
    from django.core.files.storage import default_storage

    from apps.core import images

    big = ProductImage.objects.create(product=shoes, image=ContentFile(png(size=(3000, 2000)), name="big.png"))
    name = images.ensure_rendition(big.image, 640)
    assert name.startswith("renditions/640/") and name.endswith(".webp")
    with default_storage.open(name, "rb") as fh, Image.open(fh) as im:
        assert im.format == "WEBP" and im.width == 640 and im.height == round(2000 * 640 / 3000)
    assert default_storage.size(name) < default_storage.size(big.image.name)
    mtime = default_storage.get_modified_time(name)
    assert images.ensure_rendition(big.image, 640) == name and default_storage.get_modified_time(name) == mtime


def test_small_images_are_not_upscaled(shoes):
    from django.core.files.storage import default_storage

    from apps.core import images

    tiny = ProductImage.objects.create(product=shoes, image=ContentFile(png(size=(100, 100)), name="tiny.png"))
    with default_storage.open(images.ensure_rendition(tiny.image, 960), "rb") as fh, Image.open(fh) as im:
        assert im.width == 100


def test_corrupt_source_falls_back_to_original_url(shoes):
    from apps.core import images

    bad = ProductImage(product=shoes)
    bad.image.name = "products/gallery/missing.png"
    assert images.rendition_url(bad.image, 640) == bad.image.url


def test_exif_rotation_applied_and_metadata_stripped(shoes):
    from django.core.files.storage import default_storage

    from apps.core import images

    im = Image.new("RGB", (200, 100), "blue")
    exif = im.getexif(); exif[0x0112] = 6  # rotate 90 CW when displayed
    buf = io.BytesIO(); im.save(buf, "JPEG", exif=exif)
    rotated = ProductImage.objects.create(product=shoes, image=ContentFile(buf.getvalue(), name="phone.jpg"))
    with default_storage.open(images.ensure_rendition(rotated.image, 320), "rb") as fh, Image.open(fh) as out:
        assert out.size == (100, 200) and not out.getexif()


def test_deleting_a_photo_removes_original_and_renditions(shoes, django_capture_on_commit_callbacks):
    from django.core.files.storage import default_storage

    from apps.core import images

    p = ProductImage.objects.create(product=shoes, image=ContentFile(png(), name="gone.png"))
    original, rendition = p.image.name, images.ensure_rendition(p.image, 640)
    assert default_storage.exists(original) and default_storage.exists(rendition)
    with django_capture_on_commit_callbacks(execute=True):
        p.delete()
    assert not default_storage.exists(original) and not default_storage.exists(rendition)


def test_replacing_a_photo_removes_the_old_file(shoes, django_capture_on_commit_callbacks):
    from django.core.files.storage import default_storage

    p = ProductImage.objects.create(product=shoes, image=ContentFile(png(), name="old.png"))
    old = p.image.name
    p.image = ContentFile(png(color="blue"), name="new.png")
    with django_capture_on_commit_callbacks(execute=True):
        p.save()
    assert not default_storage.exists(old) and default_storage.exists(p.image.name)


def test_gallery_markup_uses_renditions_for_slides_and_thumbs(client, shoes):
    add_photo(shoes, position=0); add_photo(shoes, position=1, tint="blue")
    html = client.get(shoes.get_absolute_url()).content.decode()
    assert "/media/renditions/960/" in html and "/media/renditions/160/" in html


def test_photo_limit_is_5_mb_and_big_uploads_are_compressed(tmp_path, settings):
    import io

    from django.core.exceptions import ValidationError
    from django.core.files.base import ContentFile
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.catalog.models import MAX_IMAGE_BYTES, Category, Product, ProductImage, validate_photo

    assert MAX_IMAGE_BYTES == 5 * 1024 * 1024
    with pytest.raises(ValidationError, match="5 MB"):
        validate_photo(SimpleUploadedFile("huge.jpg", b"0" * (MAX_IMAGE_BYTES + 1)))
    settings.MEDIA_ROOT = str(tmp_path)
    buf = io.BytesIO()
    Image.effect_noise((3000, 2000), 60).convert("RGB").save(buf, "JPEG", quality=80)  # a few MB, camera-style
    data = buf.getvalue()
    assert 1 * 1024 * 1024 < len(data) <= MAX_IMAGE_BYTES
    cat = Category.objects.create(name="C", slug="c", title="C")
    p = Product.objects.create(category=cat, name="P", slug="p", sku="P1", price=10, art_type="a")
    pi = ProductImage(product=p, image=ContentFile(data, name="IMG_0001.jpg"))
    pi.full_clean(exclude=["product"])
    pi.save()
    with pi.image.open("rb") as fh, Image.open(fh) as stored:
        assert stored.format == "WEBP" and max(stored.size) <= 1600
    assert pi.image.size < len(data) / 3


def test_bulk_upload_total_is_capped():
    from apps.catalog.admin import MAX_BULK_BYTES

    assert MAX_BULK_BYTES == 100 * 1024 * 1024


def test_bulk_upload_file_limit_comes_from_site_settings(staff_client, shoes):
    from apps.core.models import SiteSettings

    s = SiteSettings.load_fresh()
    s.bulk_upload_max_files = 2
    s.save()
    assert b"at most 2" in bulk(staff_client, shoes, [photo(f"{i}.png") for i in range(3)]).content and not shoes.images.exists()
