import importlib
import io

import pytest
from django.apps import apps as django_apps
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.urls import reverse
from PIL import Image

from apps.core.models import PromoBanner, SiteSettings
from tests.helpers import inline_management

pytestmark = pytest.mark.django_db
copy_migration = importlib.import_module("apps.core.migrations.0017_copy_promo_banner")


@pytest.fixture(autouse=True)
def fresh():
    cache.clear()


def banner(**kw):
    base = dict(title="Big sale", highlight="This week only", tag="HOT", text="<p>Save <b>more</b></p>", button_label="Shop", button_url="/shop/", position=0)
    return PromoBanner.objects.create(**{**base, **kw})


def home(client):
    return client.get("/").content.decode()


def promo_section(html):
    return html.split("promo-wrap")[1].split("</section>")[0] if "promo-wrap" in html else ""


def test_banners_render_in_order_inactive_hidden_with_their_theme(client):
    banner(title="Second", position=2, theme="plum")
    banner(title="First", position=1, theme="ocean")
    banner(title="Hidden", position=3, is_active=False)
    sec = promo_section(home(client))
    assert sec.index("First") < sec.index("Second") and "Hidden" not in sec
    assert "promo-ocean" in sec and "promo-plum" in sec


def test_slider_controls_appear_with_several_banners_only(client):
    banner()
    assert "promo-dots" not in promo_section(home(client))
    banner(title="Two", position=1)
    banner(title="Three", position=2)
    sec = promo_section(home(client))
    assert "promo-dots" in sec and "promo-prev" in sec and "promo-next" in sec and sec.count("swiper-slide") == 3


def test_banner_content_button_text_and_safety(client):
    banner(text='<p onclick="x()">ok</p><script>bad()</script>', button_url="/shop/?sort=new")
    sec = promo_section(home(client))
    assert "HOT" in sec and "Big sale" in sec and "This week only" in sec and 'href="/shop/?sort=new"' in sec
    assert "onclick" not in sec and "<script>bad" not in sec


def test_button_hidden_without_a_link(client):
    banner(button_url="")
    assert "promo-btn" not in promo_section(home(client))


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.com", "shop"])
def test_bad_button_links_rejected(url):
    with pytest.raises(ValidationError):
        PromoBanner(title="t", button_url=url).full_clean()




def test_speed_comes_from_site_settings(client):
    s = SiteSettings.load_fresh()
    s.promo_slider_seconds = 8
    s.save()
    banner()
    assert 'data-seconds="8"' in home(client)


def test_picture_is_compressed_and_removed_with_the_banner(tmp_path, settings, django_capture_on_commit_callbacks):
    from pathlib import Path

    settings.MEDIA_ROOT = str(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (2400, 800), "red").save(buf, "PNG")
    with django_capture_on_commit_callbacks(execute=True):
        b = banner(image=ContentFile(buf.getvalue(), name="wide.png"))
    with b.image.open("rb") as fh, Image.open(fh) as stored:
        assert stored.format == "WEBP" and max(stored.size) <= 1600
    with django_capture_on_commit_callbacks(execute=True):
        b.delete()
    assert not [p for p in Path(tmp_path).rglob("*") if p.is_file()]


def test_pictures_of_any_size_are_fitted_inside_the_circle():
    css = open("static/css/app.css").read()
    assert ".promo-illustration .media-img{width:min(100%,300px);height:260px;object-fit:contain" in css


# ---- the migration: current promo becomes banner 1, plus two starters so there are at least 3 --------------------------------


def test_migration_runs_once_and_never_touches_existing_banners():
    SiteSettings.load_fresh().save()
    banner(title="Mine")
    copy_migration.copy_promo(django_apps, None)
    copy_migration.copy_promo(django_apps, None)
    assert list(PromoBanner.objects.values_list("title", flat=True)) == ["Mine"]




# ---- admin -------------------------------------------------------------------------------------------------------------------


def test_no_banners_means_no_promo_section(client):
    assert "promo-wrap" not in home(client) and "promo-text" not in home(client)


def test_migration_copies_the_current_promo_as_banner_one_and_no_sample_banners(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    s = SiteSettings.load_fresh()
    s.promo_tag, s.promo_title, s.promo_highlight, s.promo_text, s.promo_button, s.promo_art = "MY TAG", "My promo", "My line", "<p>Hi</p>", "Go", "bag"
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buf, "PNG")
    s.promo_image.save("promo-old.png", ContentFile(buf.getvalue()), save=False)
    s.save()
    before = SiteSettings.objects.values().get()
    copy_migration.copy_promo(django_apps, None)
    first = PromoBanner.objects.get()   # exactly one: no sample banners are invented
    assert (first.tag, first.title, first.highlight, first.text, first.button_label, first.art) == ("MY TAG", "My promo", "My line", "<p>Hi</p>", "Go", "bag")
    assert first.button_url == "#categories" and first.theme == "forest" and first.image and first.image.name != SiteSettings.objects.get().promo_image.name
    assert SiteSettings.objects.values().get() == before   # Site settings untouched


def test_a_missing_picture_file_does_not_break_the_migration(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    s = SiteSettings.load_fresh()
    s.promo_image.name = "site/gone.png"
    s.save()
    copy_migration.copy_promo(django_apps, None)
    assert PromoBanner.objects.count() == 1 and not PromoBanner.objects.get().image


def test_only_untouched_sample_banners_are_removed_by_the_cleanup_migration():
    cleanup = importlib.import_module("apps.core.migrations.0019_remove_sample_banners")
    untouched = PromoBanner.objects.create(**cleanup.SAMPLES[0])
    edited = PromoBanner.objects.create(**{**cleanup.SAMPLES[1], "highlight": "my own words"})
    mine = banner(title="Mine")
    cleanup.remove_samples(django_apps, None)
    remaining = set(PromoBanner.objects.values_list("pk", flat=True))
    assert untouched.pk not in remaining and edited.pk in remaining and mine.pk in remaining


def test_custom_background_and_text_color_are_applied(client):
    banner(bg_color="#101820", bg_color2="#2b5876", text_style="dark")
    sec = promo_section(home(client))
    assert "linear-gradient(103deg,#101820,#2b5876)" in sec and "is-dark" in sec


def test_default_look_without_custom_background(client):
    banner()
    sec = promo_section(home(client))
    assert "linear-gradient" not in sec and "is-dark" not in sec and "promo-forest" in sec


def test_the_seed_gives_a_new_shop_one_promo_banner_and_no_more():
    from django.core.management import call_command

    call_command("seed_catalog", dataset="test")
    call_command("seed_catalog", dataset="test")
    assert PromoBanner.objects.count() == 1


def test_promo_banners_are_edited_inline_in_site_settings_with_add_another(client):
    from django.contrib.auth import get_user_model

    from apps.accounts.twofactor import VERIFIED_KEY

    user = get_user_model().objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(user)
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    SiteSettings.load_fresh()
    html = client.get(reverse("admin:core_sitesettings_change", args=[1])).content.decode()
    assert "Promo banners" in html and "promo_banners-TOTAL_FORMS" in html and "Add another Promo banner" in html
    for field in ("tag", "title", "highlight", "text", "button_label", "button_url", "image", "art", "theme", "bg_color", "bg_color2", "text_style"):
        assert f"promo_banners-__prefix__-{field}" in html
    assert "Old promo banner" not in html and "Old banner" not in html


def test_adding_a_promo_banner_in_site_settings(client):
    from django.contrib.auth import get_user_model
    from django.forms.models import model_to_dict

    from apps.accounts.twofactor import VERIFIED_KEY

    user = get_user_model().objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(user)
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    site = SiteSettings.load_fresh()
    data = {k: ("" if v is None else v) for k, v in model_to_dict(site).items() if k not in ("id", "hero_image", "promo_image", "story_image")}
    data.update(inline_management(promo_banners=(1, 0)))
    data.update({"promo_banners-0-id": "", "promo_banners-0-site": 1, "promo_banners-0-title": "Added in settings", "promo_banners-0-is_active": "on", "promo_banners-0-position": 1,
                 "promo_banners-0-theme": "ocean", "promo_banners-0-bg_color": "#0f2a44", "promo_banners-0-text_style": "light", "promo_banners-0-art": "lamp"})
    resp = client.post(reverse("admin:core_sitesettings_change", args=[1]), data)
    assert resp.status_code == 302
    b = PromoBanner.objects.get()
    assert b.title == "Added in settings" and b.site_id == 1 and b.bg_color == "#0f2a44" and b.theme == "ocean"
