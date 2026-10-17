"""Keep uploaded photos tidy: pre-build renditions after save, delete files (and renditions) when rows or photos go away."""
import logging

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save

from . import images

logger = logging.getLogger("apps.core")

# (app_label.Model, image field names)
IMAGE_FIELDS = {
    "catalog.ProductImage": ("image",),
    "catalog.Product": ("image",),
    "catalog.ProductVariant": ("image",),
    "catalog.Category": ("image",),
    "core.SiteSettings": ("hero_image", "promo_image", "story_image"),
    "core.HeroSlide": ("image",),
    "core.PromoBanner": ("image",),
}


def _remove(name: str) -> None:
    from django.core.files.storage import default_storage

    try:
        default_storage.delete(name)
    except Exception:  # noqa: BLE001
        logger.warning("could not delete file %s", name)
    images.delete_renditions(name)


def _remember_old(sender, instance, **kwargs):
    """Before an update, note files that are about to be replaced or cleared."""
    instance._old_image_names = []
    for field in IMAGE_FIELDS[f"{sender._meta.label}"]:
        images.shrink_upload(getattr(instance, field))
    if not instance.pk:
        return
    old = sender.objects.filter(pk=instance.pk).first()
    if old is None:
        return
    for field in IMAGE_FIELDS[f"{sender._meta.label}"]:
        before, after = getattr(old, field), getattr(instance, field)
        if before and before.name != getattr(after, "name", ""):
            instance._old_image_names.append(before.name)


def _after_save(sender, instance, **kwargs):
    for name in getattr(instance, "_old_image_names", []):
        transaction.on_commit(lambda n=name: _remove(n))
    for field in IMAGE_FIELDS[sender._meta.label]:
        file = getattr(instance, field)
        if file:
            transaction.on_commit(lambda f=file: images.pregenerate(f))


def _after_delete(sender, instance, **kwargs):
    for field in IMAGE_FIELDS[sender._meta.label]:
        file = getattr(instance, field)
        if file:
            transaction.on_commit(lambda n=file.name: _remove(n))


def connect() -> None:
    for label in IMAGE_FIELDS:
        model = apps.get_model(label)
        pre_save.connect(_remember_old, sender=model, dispatch_uid=f"img-pre-{label}")
        post_save.connect(_after_save, sender=model, dispatch_uid=f"img-save-{label}")
        post_delete.connect(_after_delete, sender=model, dispatch_uid=f"img-del-{label}")
