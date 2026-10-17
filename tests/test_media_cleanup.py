import io
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

from apps.catalog.models import Category, Product, ProductImage
from apps.core import images

pytestmark = pytest.mark.django_db


def photo(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def product(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    cat = Category.objects.create(name="C", slug="c", title="C")
    return Product.objects.create(category=cat, name="P", slug="p", sku="P1", price=10, art_type="a", image=ContentFile(photo(), name="main.jpg"))


def stored_files(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


def test_deleting_a_product_removes_every_photo_and_rendition(product, settings, django_capture_on_commit_callbacks):
    for i in range(3):
        ProductImage.objects.create(product=product, image=ContentFile(photo("blue"), name=f"g{i}.jpg"), position=i)
    images.ensure_rendition(product.cover_image, 320)
    assert len(stored_files(settings.MEDIA_ROOT)) == 5  # main photo, 3 gallery photos, 1 rendition
    with django_capture_on_commit_callbacks(execute=True):
        product.delete()
    assert stored_files(settings.MEDIA_ROOT) == []


def test_bulk_delete_from_the_admin_list_also_cleans_up(product, settings, django_capture_on_commit_callbacks):
    ProductImage.objects.create(product=product, image=ContentFile(photo(), name="g.jpg"))
    with django_capture_on_commit_callbacks(execute=True):
        Product.objects.filter(pk=product.pk).delete()  # what the admin "delete selected" action does
    assert stored_files(settings.MEDIA_ROOT) == []


def test_replacing_or_removing_one_photo_deletes_the_old_file(product, settings, django_capture_on_commit_callbacks):
    pi = ProductImage.objects.create(product=product, image=ContentFile(photo(), name="old.jpg"))
    old = pi.image.name
    with django_capture_on_commit_callbacks(execute=True):
        pi.delete()
    assert not default_storage.exists(old)
    assert product.image and default_storage.exists(product.image.name)  # other photos untouched
