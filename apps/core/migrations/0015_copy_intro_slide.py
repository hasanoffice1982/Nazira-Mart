"""Copies the current home banner (the intro slide stored in Site settings) into the new Hero slides table as slide 1.

Additive and safe: nothing is deleted or changed in Site settings, it only runs when there are no slides yet, and the picture is COPIED
to its own file so removing one never deletes the other's file."""
import os

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations


def copy_intro(apps, schema_editor):
    SiteSettings = apps.get_model("core", "SiteSettings")
    HeroSlide = apps.get_model("core", "HeroSlide")
    Product = apps.get_model("catalog", "Product")
    site = SiteSettings.objects.first()
    if site is None or HeroSlide.objects.exists():
        return
    slide = HeroSlide(
        is_active=True, position=0, eyebrow=site.hero_eyebrow, title=site.hero_title, highlight=site.hero_highlight, text=site.hero_text,
        button_label=site.hero_button_primary, button_url="#featured", button2_label=site.hero_button_secondary, button2_url="#categories",
        art=site.hero_art, proof_text=site.hero_proof_text, float_title="Fresh finds", float_text="Just for you", bubble_label="Featured this week",
        product=Product.objects.filter(is_active=True, art_type="headphones").first(),
    )
    if site.hero_image:
        try:
            with default_storage.open(site.hero_image.name, "rb") as fh:
                data = fh.read()
            slide.image.save("hero/" + os.path.basename(site.hero_image.name), ContentFile(data), save=False)
        except Exception:  # noqa: BLE001  (a missing file must never block the deploy; the illustration is used instead)
            slide.image = ""
    slide.save()


class Migration(migrations.Migration):
    dependencies = [("core", "0014_heroslide"), ("catalog", "0010_pending_photos")]
    operations = [migrations.RunPython(copy_intro, migrations.RunPython.noop)]
