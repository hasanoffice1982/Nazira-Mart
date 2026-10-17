from django.db import migrations

from apps.core.richtext import looks_like_html, rich


def to_html(apps, schema_editor):
    site = apps.get_model("core", "SiteSettings").objects.first()
    if site is None:
        return
    updates = {}
    for field in ("hero_text", "promo_text", "footer_about"):
        value = getattr(site, field)
        if value and not looks_like_html(value):
            updates[field] = str(rich(value))
    if updates:
        apps.get_model("core", "SiteSettings").objects.filter(pk=site.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [("core", "0007_page_body_help")]
    operations = [migrations.RunPython(to_html, migrations.RunPython.noop)]
