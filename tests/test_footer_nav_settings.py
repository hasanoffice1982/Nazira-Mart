import pytest
from django.core import mail
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


def test_header_menu_respects_flag_order_and_max(client):
    cats = _cats()
    cats[0].show_in_nav = False
    cats[0].save()
    html = client.get("/").content.decode()
    nav = html.split('class="nav-wrap"')[1].split("</nav>")[0]
    assert "category=cat0" not in nav
    assert all(f"category=cat{i}" in nav for i in (1, 2, 3, 4)) and "category=cat5" not in nav
    _edit(nav_max_categories=2)
    nav = client.get("/").content.decode().split('class="nav-wrap"')[1].split("</nav>")[0]
    assert "category=cat2" in nav and "category=cat3" not in nav


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
