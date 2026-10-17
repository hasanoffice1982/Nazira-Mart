from django.db import migrations

from apps.core.richtext import looks_like_html, rich


def to_html(apps, schema_editor):
    """Older plain-text content becomes HTML once, so the editor shows it formatted."""
    for model, field in ((apps.get_model("core", "Page"), "body"), (apps.get_model("core", "SiteSettings"), "story_text"), (apps.get_model("catalog", "Product"), "description")):
        for obj in model.objects.all():
            value = getattr(obj, field)
            if value and not looks_like_html(value):
                model.objects.filter(pk=obj.pk).update(**{field: str(rich(value))})


class Migration(migrations.Migration):
    dependencies = [("core", "0005_header_menu"), ("catalog", "0005_header_menu")]
    operations = [migrations.RunPython(to_html, migrations.RunPython.noop)]
