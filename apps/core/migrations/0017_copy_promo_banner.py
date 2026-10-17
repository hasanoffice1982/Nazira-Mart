"""Copies the current promo banner (stored in Site settings) into the new Promo banners table as banner 1 as banner 1. More banners are added by the admin with "+ Add another" in Site settings.

Additive and safe: nothing in Site settings is changed or deleted, it only runs when there are no promo banners yet, and the picture is
COPIED to its own file so removing one never deletes the other's file."""
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations


def copy_promo(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    PromoBanner = apps.get_model("core", "PromoBanner")
    site = SiteSettings.objects.first()
    if site is None or PromoBanner.objects.exists():
        return
    first = PromoBanner(
        is_active=True, position=0, tag=site.promo_tag, title=site.promo_title, highlight=site.promo_highlight, text=site.promo_text,
        button_label=site.promo_button, button_url="#categories", art=site.promo_art, theme="forest",
    )
    if site.promo_image:
        try:
            with default_storage.open(site.promo_image.name, "rb") as fh:
                data = fh.read()
            first.image.save("promo/" + os.path.basename(site.promo_image.name), ContentFile(data), save=False)
        except Exception:  # noqa: BLE001  (a missing file must never block the deploy; the illustration is used instead)
            first.image = ""
    first.save()


class Migration(migrations.Migration):
    dependencies = [("core", "0016_promobanner")]
    operations = [migrations.RunPython(copy_promo, migrations.RunPython.noop)]
