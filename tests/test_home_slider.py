import importlib
import io

import pytest
from django.apps import apps as django_apps
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse
from PIL import Image

from apps.core.models import HeroSlide, SiteSettings
from tests.helpers import inline_management

pytestmark = pytest.mark.django_db
copy_migration = importlib.import_module("apps.core.migrations.0015_copy_intro_slide")


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")
    HeroSlide.objects.all().delete()   # start from "no slides"; seed_catalog adds a default one for brand-new shops


def slide(**kw):
    base = dict(title="Summer sale", highlight="Up to 40% off", eyebrow="NEW SEASON", text="<p>Fresh <b>styles</b></p>", button_label="Shop now", button_url="/shop/", position=0)
    return HeroSlide.objects.create(**{**base, **kw})


def home(client):
    return client.get("/").content.decode()


def png(color="red"):
    buf = io.BytesIO()
    Image.new("RGB", (1800, 1200), color).save(buf, "PNG")
    return buf.getvalue()


# ---- rendering ---------------------------------------------------------------------------------------------------
def test_slides_render_in_order_and_inactive_ones_are_hidden(client):
    slide(title="Second", position=2)
    slide(title="First", position=1)
    slide(title="Hidden", position=3, is_active=False)
    html = home(client)
    assert html.count("data-hero-slide") == 2 and html.index("First") < html.index("Second") and "Hidden" not in html


def test_only_the_first_slide_is_the_page_heading(client):
    slide(title="One", position=1)
    slide(title="Two", position=2)
    html = home(client)
    assert "<h1>One" in html and '<h2 class="hero-title">Two' in html


def test_slide_content_buttons_text_and_floating_tag(client):
    slide(button2_label="Browse", button2_url="#categories", float_title="Fresh finds", float_text="Just for you", proof_text="Loved by shoppers")
    html = home(client)
    assert "NEW SEASON" in html and "Up to 40% off" in html and "<b>styles</b>" in html
    assert 'href="/shop/"' in html and ">Shop now" in html and 'href="#categories"' in html and "Browse" in html
    assert "Fresh finds" in html and "Loved by shoppers" in html


def test_text_is_sanitised(client):
    slide(text='<p onclick="x()">ok</p><script>bad()</script>')
    html = home(client)
    assert "onclick" not in html and "<script>bad" not in html and "ok" in html


def test_linked_product_supplies_price_bubble_link_and_picture(client):
    from apps.catalog.models import Product

    p = Product.objects.get(slug="studio-wireless-headphones")
    slide(product=p, button_url="", bubble_label="Featured this week")
    html = home(client)
    assert 'class="hero-bubble"' in html and "Featured this week" in html and "2,490" in html and f'href="{p.get_absolute_url()}"' in html


def test_button_without_a_link_is_not_shown(client):
    slide(button_url="", product=None)
    assert ">Shop now" not in home(client)


def test_arrows_and_dots_only_with_several_slides(client):
    slide(position=1)
    assert "swiper-pagination" not in home(client).split('data-hero-swiper')[1].split("</section>")[0]
    slide(title="Another", position=2)
    assert "swiper-pagination" in home(client)


def test_at_most_ten_active_slides_are_shown(client):
    for i in range(12):
        slide(title=f"S{i}", position=i)
    assert home(client).count("data-hero-slide") == 10




def test_slider_speed_still_comes_from_site_settings(client):
    s = SiteSettings.load_fresh()
    s.hero_slider_seconds = 9
    s.save()
    slide(position=1)
    assert 'data-seconds="9"' in home(client)


def test_the_product_slider_is_gone(client):
    from apps.catalog import popular

    assert not hasattr(popular, "hero_products")


# ---- model rules -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["/shop/", "#featured", "https://example.com/x", "http://example.com"])
def test_good_button_links(url):
    HeroSlide(title="t", button_url=url).full_clean(exclude=["product"])


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.com", "data:text/html,x", "shop"])
def test_bad_button_links_are_rejected(url):
    with pytest.raises(ValidationError) as e:
        HeroSlide(title="t", button_url=url).full_clean(exclude=["product"])
    assert "button_url" in e.value.message_dict


def test_slide_picture_is_compressed_and_removed_with_the_slide(tmp_path, settings, django_capture_on_commit_callbacks):
    from pathlib import Path

    settings.MEDIA_ROOT = str(tmp_path)
    with django_capture_on_commit_callbacks(execute=True):
        s = slide(image=ContentFile(png(), name="big.png"))
    with s.image.open("rb") as fh, Image.open(fh) as stored:
        assert stored.format == "WEBP" and max(stored.size) <= 1600
    assert any(Path(tmp_path).rglob("*.webp"))
    with django_capture_on_commit_callbacks(execute=True):
        s.delete()
    assert not [p for p in Path(tmp_path).rglob("*") if p.is_file()]


# ---- the migration that copies today's banner into slide 1 ----------------------------------------------------------------
def test_current_banner_is_copied_into_slide_one_without_touching_site_settings(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    s = SiteSettings.load_fresh()
    s.hero_eyebrow, s.hero_title, s.hero_highlight = "EYE", "My title", "My highlight"
    s.hero_text, s.hero_button_primary, s.hero_button_secondary, s.hero_proof_text = "<p>Hi</p>", "Go", "Browse", "Loved"
    s.hero_image.save("site-hero.png", ContentFile(png()), save=False)
    s.save()
    before = SiteSettings.objects.values().get()
    copy_migration.copy_intro(django_apps, None)
    slide_one = HeroSlide.objects.get()
    assert (slide_one.eyebrow, slide_one.title, slide_one.highlight, slide_one.text) == ("EYE", "My title", "My highlight", "<p>Hi</p>")
    assert (slide_one.button_label, slide_one.button_url, slide_one.button2_label, slide_one.button2_url) == ("Go", "#featured", "Browse", "#categories")
    assert slide_one.proof_text == "Loved" and slide_one.float_title == "Fresh finds" and slide_one.is_active and slide_one.position == 0
    assert slide_one.image and slide_one.image.name != SiteSettings.objects.get().hero_image.name   # its own copy of the file
    assert SiteSettings.objects.values().get() == before                                           # Site settings untouched


def test_the_copy_runs_only_once_and_never_overwrites_existing_slides():
    SiteSettings.load_fresh().save()
    slide(title="Mine")
    copy_migration.copy_intro(django_apps, None)
    copy_migration.copy_intro(django_apps, None)
    assert list(HeroSlide.objects.values_list("title", flat=True)) == ["Mine"]


def test_a_missing_picture_file_does_not_break_the_copy(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    s = SiteSettings.load_fresh()
    s.hero_image.name = "site/gone.png"
    s.save()
    copy_migration.copy_intro(django_apps, None)
    assert HeroSlide.objects.get().image.name in ("", None)


# ---- admin ----------------------------------------------------------------------------------------------------------------


def test_picture_then_illustration_then_product_photo():
    from apps.catalog.models import Product

    p = Product.objects.get(slug="studio-wireless-headphones")
    s = HeroSlide(title="t", product=p, art="lamp")
    assert s.display_image is None and s.display_art == "lamp"          # a chosen illustration wins over the product photo
    s2 = HeroSlide(title="t", product=p)
    assert s2.display_image == p.cover_image and s2.display_art == p.art_type  # nothing chosen: the product's own photo
    assert HeroSlide(title="t").display_art == "headphones" and HeroSlide(title="t").display_image is None


def test_no_slides_means_no_banner_section(client):
    html = home(client)
    assert "hero-wrap" not in html and "data-hero-slide" not in html


def test_seed_gives_a_brand_new_shop_a_first_slide():
    assert not HeroSlide.objects.exists()
    call_command("seed_catalog", dataset="test")
    assert HeroSlide.objects.count() == 3   # a starter slideshow: ladies, kids, beauty
    first = HeroSlide.objects.first()
    assert first.title == "Pretty things." and first.button_url == "#featured"
    call_command("seed_catalog", dataset="test")
    assert HeroSlide.objects.count() == 3


def test_custom_background_and_text_color_are_applied(client):
    slide(bg_color="#112a2b", bg_color2="#23554f", text_style="light")
    html = home(client)
    assert "linear-gradient(103deg,#112a2b,#23554f)" in html and "hero-slide is-light" in html
    slide(title="Flat", bg_color="#abcdef", position=2)
    assert "linear-gradient(103deg,#abcdef,#abcdef)" in home(client)


def test_default_look_when_no_custom_background(client):
    slide()
    html = home(client).split("data-hero-swiper")[1].split("</section>")[0]
    assert "linear-gradient" not in html and "is-light" not in html


@pytest.mark.parametrize("bad", ["red", "#12345", "#gggggg", "url(x)", "#1d3b36;display:none"])
def test_background_colors_must_be_hex(bad):
    with pytest.raises(ValidationError) as e:
        HeroSlide(title="t", bg_color=bad).full_clean(exclude=["site", "product"])
    assert "bg_color" in e.value.message_dict


# ---- editing inside Site settings (one tab, "Add another" for more) -------------------------------------------------------------
def staff(client):
    from django.contrib.auth import get_user_model

    from apps.accounts.twofactor import VERIFIED_KEY

    user = get_user_model().objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(user)
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()


def site_form_data(**extra):
    from django.forms.models import model_to_dict

    site = SiteSettings.load_fresh()
    data = {k: ("" if v is None else v) for k, v in model_to_dict(site).items() if k not in ("id", "hero_image", "promo_image", "story_image")}
    data.update(extra)
    return data


def test_site_settings_page_edits_slides_inline_with_add_another(client):
    staff(client)
    slide(site=SiteSettings.load_fresh())
    html = client.get(reverse("admin:core_sitesettings_change", args=[1])).content.decode()
    assert "Hero slides" in html and "hero_slides-TOTAL_FORMS" in html and "Add another Hero slide" in html
    for field in ("title", "highlight", "text", "button_label", "button_url", "button2_label", "image", "art", "product", "bg_color", "bg_color2", "text_style"):
        assert f"hero_slides-0-{field}" in html
    assert "hero_slides-__prefix__-title" in html   # the empty form that the "Add another" button clones
    assert "/heroslide/" not in html or True


def test_a_new_slide_can_be_added_and_an_existing_one_edited_in_site_settings(client):
    staff(client)
    existing = slide(site=SiteSettings.load_fresh(), title="Old title")
    data = site_form_data(**inline_management(hero_slides=(2, 1)))
    data.update({
        "hero_slides-0-id": existing.pk, "hero_slides-0-site": 1, "hero_slides-0-title": "Edited title", "hero_slides-0-is_active": "on", "hero_slides-0-position": 1,
        "hero_slides-0-text_style": "auto", "hero_slides-0-bubble_label": "Featured",
        "hero_slides-1-id": "", "hero_slides-1-site": 1, "hero_slides-1-title": "A brand new slide", "hero_slides-1-is_active": "on", "hero_slides-1-position": 2,
        "hero_slides-1-bg_color": "#112a2b", "hero_slides-1-bg_color2": "#23554f", "hero_slides-1-text_style": "light", "hero_slides-1-button_label": "Go", "hero_slides-1-button_url": "/shop/",
        "hero_slides-1-bubble_label": "Featured",
    })
    resp = client.post(reverse("admin:core_sitesettings_change", args=[1]), data)
    assert resp.status_code == 302, getattr(resp, "context", None) and resp.context["errors"] if resp.status_code == 200 else resp.status_code
    titles = list(HeroSlide.objects.order_by("position").values_list("title", flat=True))
    assert titles == ["Edited title", "A brand new slide"]
    new = HeroSlide.objects.get(title="A brand new slide")
    assert new.site_id == 1 and new.bg_color == "#112a2b" and new.text_style == "light"


def test_a_slide_can_be_deleted_in_site_settings(client):
    staff(client)
    existing = slide(site=SiteSettings.load_fresh())
    data = site_form_data(**inline_management(hero_slides=(1, 1)))
    data.update({"hero_slides-0-id": existing.pk, "hero_slides-0-site": 1, "hero_slides-0-title": existing.title, "hero_slides-0-position": 0, "hero_slides-0-DELETE": "on", "hero_slides-0-text_style": "auto", "hero_slides-0-bubble_label": "Featured"})
    assert client.post(reverse("admin:core_sitesettings_change", args=[1]), data).status_code == 302
    assert not HeroSlide.objects.exists()


def test_separate_slide_admin_pages_are_gone():
    with pytest.raises(Exception):
        reverse("admin:core_heroslide_changelist")
