"""Links the existing hero slides and promo banners to the one Site settings row so they can be edited inline there. Additive: only fills the
new link column; nothing else is changed or deleted."""
from django.db import migrations


def attach(apps, schema_editor):
    site = apps.get_model("core", "SiteSettings").objects.first()
    if site is None:
        return
    apps.get_model("core", "HeroSlide").objects.filter(site__isnull=True).update(site=site)
    apps.get_model("core", "PromoBanner").objects.filter(site__isnull=True).update(site=site)


class Migration(migrations.Migration):
    dependencies = [("core", "0020_slides_site_link")]
    operations = [migrations.RunPython(attach, migrations.RunPython.noop)]
