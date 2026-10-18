import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse

from apps.catalog.models import Category
from apps.core.models import HeroSlide, SiteSettings

pytestmark = pytest.mark.django_db


def _cats(n=6):
    return [Category.objects.create(name=f"Cat{i}", slug=f"cat{i}", title=f"Cat{i}", position=i) for i in range(n)]


def _edit(**kw):
    s = SiteSettings.load_fresh()
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()


def test_defaults_render_current_copy(client):
    html = client.get("/").content.decode()
    assert "Pretty finds for ladies and little ones." in html
    assert "Shop by category" in html
    assert "All rights reserved" in html


def test_edited_footer_and_headings_show(client):
    HeroSlide.objects.create(title="Welcome", button_label="Go now", button_url="/shop/")
    _edit(company_name="Acme", footer_about="About <b>us</b>", categories_title="Aisles", contact_phone="+880 1712-345678")
    html = client.get("/").content.decode()
    assert "Acme. All rights reserved" in html
    assert "About <b>us</b>" in html
    assert "Go now" in html and "Aisles" in html
    assert 'href="tel:+8801712-345678"' in html


def test_only_filled_social_links_render(client):
    assert 'aria-label="Facebook"' not in client.get("/").content.decode()
    _edit(facebook_url="https://facebook.com/narizmart")
    html = client.get("/").content.decode()
    assert 'href="https://facebook.com/narizmart"' in html and 'aria-label="Instagram"' not in html


def test_social_url_rejects_non_http_and_phone_validated():
    s = SiteSettings.load_fresh()
    s.facebook_url = "javascript:alert(1)"
    s.contact_phone = "abc<script>"
    with pytest.raises(Exception) as e:
        s.full_clean()
    assert "facebook_url" in str(e.value) and "contact_phone" in str(e.value)


def test_contact_email_falls_back_and_receives_form(client, settings):
    settings.SUPPORT_EMAIL = "fallback@example.com"
    assert SiteSettings.load_fresh().effective_contact_email == "fallback@example.com"
    _edit(contact_email="shop@example.com")
    assert "shop@example.com" in client.get("/").content.decode()
    client.post(reverse("core:contact"), {"name": "A", "email": "a@b.com", "subject": "Hi", "message": "Hello there friend"})
    assert mail.outbox and mail.outbox[-1].to == ["shop@example.com"]


def test_shop_all_dropdown_lists_every_flagged_category_in_order(client):
    cats = _cats()
    cats[0].show_in_nav = False
    cats[0].save()
    html = client.get("/").content.decode()
    nav = html.split('class="nav-wrap"')[1].split("</nav>")[0]
    assert "category=cat0" not in nav
    assert all(f"category=cat{i}" in nav for i in (1, 2, 3, 4, 5))   # the dropdown shows all of them, not just the first few
    assert nav.index("category=cat1") < nav.index("category=cat2") < nav.index("category=cat5")
    for name in ("collection=new", "collection=gifts", "collection=sale"):
        assert name in nav


def test_footer_pinned_css_present():
    css = open("static/css/narizmart.css").read()
    assert "min-height:100dvh" in css and "flex-direction:column" in css


def test_category_admin_sortable_and_nav_toggle(admin_client):
    from apps.accounts.twofactor import VERIFIED_KEY

    session = admin_client.session
    session[VERIFIED_KEY] = True
    session.save()
    _cats(2)
    r = admin_client.get(reverse("admin:catalog_category_changelist"))
    assert r.status_code == 200 and b"show_in_nav" in r.content


def test_categories_are_a_simple_slider_with_arrow_icons_and_dots(client):
    _cats(3)
    html = client.get("/").content.decode()
    section = html.split('id="categories"')[1].split("</section>")[0]
    assert "data-category-swiper" in section and section.count("swiper-slide") == 3
    assert "cat-prev" in section and "cat-next" in section and "cat-dots" in section and "cat-scrollbar" not in section and "category=cat0" in section


def test_category_and_hero_pictures_are_fitted_inside_their_circle_not_cropped():
    css = open("static/css/app.css").read()
    assert ".category-visual .media-img{position:absolute;" in css and "object-fit:contain" in css.split(".category-visual .media-img{position:absolute;")[1].split("}")[0]
    assert ".hero-object img{object-fit:contain" in css


def test_category_dots_do_not_use_dynamic_bullets_and_wrap_between_the_arrows():
    js = open("static/js/app.js").read()
    assert "dynamicBullets" not in js
    css = open("static/css/app.css").read()
    assert ".cat-nav .cat-dots{flex:0 1 auto;width:auto;" in css and "flex-wrap:wrap" in css.split(".cat-nav .cat-dots{flex:0 1 auto;width:auto;")[1].split("}")[0]


# ---- collections (Shop All dropdown) and layout options ----------------------------------------------------------
def test_collections_filter_gifts_sale_and_new(client):
    from apps.catalog.models import Product

    call_command("seed_catalog", dataset="test")
    a, b, c = list(Product.objects.order_by("id")[:3])
    Product.objects.filter(pk=a.pk).update(is_gift=True, old_price=None)
    Product.objects.filter(pk=b.pk).update(is_gift=False, old_price=1, price=500)    # old price below price: not a discount
    Product.objects.filter(pk=c.pk).update(old_price=9000, price=100, is_gift=False)
    gifts = client.get("/shop/?collection=gifts").content.decode()
    assert a.name in gifts and b.name not in gifts and "Gift ideas" in gifts
    sale = client.get("/shop/?collection=sale").content.decode()
    assert c.name in sale and b.name not in sale and "Discounted products" in sale
    assert "New arrivals" in client.get("/shop/?collection=new").content.decode()
    assert "New arrivals" in client.get("/shop/?sort=new").content.decode()   # old links keep working


def test_collection_pages_have_titles_canonical_and_sitemap_entries(client, settings):
    settings.SITE_URL = "https://narizmart.com"
    html = client.get("/shop/?collection=sale&sort=price_asc").content.decode()
    assert "<title>Discounted products — NarizMart</title>" in html and 'rel="canonical" href="https://narizmart.com/shop/?collection=sale"' in html
    sitemap = client.get("/sitemap.xml").content.decode()
    assert all(f"collection={x}" in sitemap for x in ("new", "gifts", "sale"))


def test_menu_bar_is_short_and_the_dropdown_holds_collections(client):
    html = client.get("/").content.decode()
    bar = html.split('class="nav-wrap"')[1].split("</nav>")[0]
    assert "Shop All" in bar and "Track order" in bar and 'role="menu"' in bar
    for label in ("New arrivals", "Gift ideas", "Discounted products"):
        assert label in bar


def test_layout_options_default_to_wide_and_can_be_switched(client):
    assert 'class="layout-wide hero-normal"' in client.get("/").content.decode()
    assert "--side-pad:96px" in client.get("/").content.decode()
    _edit(layout_mode="boxed", layout_side_padding=100, layout_hero_height="tall")
    html = client.get("/").content.decode()
    assert 'class="layout-boxed hero-tall"' in html and "--side-pad:100px" in html


def test_layout_fields_are_in_the_admin(client, django_user_model):
    staff = django_user_model.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    session = client.session
    session["2fa_verified"] = True
    session.save()
    SiteSettings.load_fresh()
    html = client.get(reverse("admin:core_sitesettings_change", args=[1])).content.decode()
    for name in ("layout_mode", "layout_side_padding", "layout_hero_height"):
        assert f'name="{name}"' in html
    assert "Layout &amp; size" in html
