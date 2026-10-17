"""Removes the two sample promo banners that an earlier version of this change added, but ONLY when they are still exactly as created
(never edited by the admin). Anything the admin changed or wrote is left alone."""
from django.db import migrations

SAMPLES = [
    dict(tag="SHOP WITH CONFIDENCE", title="Cash on delivery", highlight="across Bangladesh.", button_url="/shop/", theme="ocean",
         text="Order today, check your parcel when it arrives and pay the courier in cash."),
    dict(tag="NEW ARRIVALS ✦", title="Fresh finds,", highlight="all the time.", button_url="/shop/?sort=new", theme="plum",
         text="New products land in the shop regularly. Be the first to see what is new."),
]


def remove_samples(apps, schema_editor):
    PromoBanner = apps.get_model("core", "PromoBanner")
    for sample in SAMPLES:
        PromoBanner.objects.filter(**sample).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0018_slide_backgrounds")]
    operations = [migrations.RunPython(remove_samples, migrations.RunPython.noop)]
