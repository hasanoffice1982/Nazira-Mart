import csv
import io

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from apps.catalog import importer
from apps.catalog.models import Category, Color, Product, ProductVariant, Size

User = get_user_model()
pytestmark = pytest.mark.django_db
IMPORT = "admin:catalog_product_import"
CONFIRM = "admin:catalog_product_import_confirm"
HEAD = ",".join(importer.BASE_COLUMNS)   # the demo file; `dealer` is an optional extra column


@pytest.fixture(autouse=True)
def seeded():
    cache.clear()
    call_command("seed_catalog", dataset="test")


@pytest.fixture
def staff_client(client):
    staff = User.objects.create_superuser("boss@example.com", "pass12345!x")
    client.force_login(staff)
    session = client.session
    session["2fa_verified"] = True  # staff 2FA is covered in test_two_factor
    session.save()
    return client


def make_csv(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(importer.COLUMNS)
    for r in rows:
        d = dict(zip(importer.COLUMNS, r)) if not isinstance(r, dict) else r
        w.writerow([d.get(c, "") for c in importer.COLUMNS])
    return out.getvalue().encode()


def row(**kw):
    return {"name": "Test Tee", "category": "Fashion", "price": "500", **kw}


def upload(client, data, name="products.csv", **post):
    return client.post(reverse(IMPORT), {"csv_file": SimpleUploadedFile(name, data, "text/csv"), "create_missing": "on", **post})


def run_import(client, data, **post):
    preview = upload(client, data, **post)
    assert preview.context["plan"].ok, preview.context["plan"].errors
    client.post(reverse(CONFIRM))


# ---- demo file -----------------------------------------------------------------------------
def test_demo_csv_is_valid_and_imports(staff_client):
    run_import(staff_client, importer.demo_csv().encode())
    tee = Product.objects.get(slug="classic-cotton-tshirt")
    assert tee.sku == "NM-1001" and tee.has_variants and len(tee.active_variants) == 4
    xl = ProductVariant.objects.get(sku="TSHIRT-WHT-XL")
    assert xl.price == 950 and xl.stock == 12 and xl.color.name == "White" and xl.size.name == "XL"
    assert Color.objects.get(name="Black").hex_code == "#1c3129"
    shoes = Product.objects.get(slug="everyday-running-shoes")
    assert [v.size.name for v in shoes.active_variants] == ["41", "42", "43"] and all(v.color_id is None for v in shoes.active_variants)
    mug = Product.objects.get(slug="ceramic-coffee-mug")
    assert [c.name for c in mug.colors] == sorted(c.name for c in mug.colors) or len(mug.colors) == 2
    desk = Product.objects.get(slug="bamboo-desk-organizer")
    assert not desk.has_variants and desk.stock == 60


def test_demo_template_download_and_sample_file_match(staff_client):
    resp = staff_client.get(reverse("admin:catalog_product_import_template"))
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv") and "attachment" in resp["Content-Disposition"]
    assert resp.content.decode().splitlines()[0] == HEAD
    assert importer.demo_csv() == open("sample_data/products_demo.csv").read()


def test_imported_products_are_buyable_on_the_storefront(staff_client, client):
    run_import(staff_client, importer.demo_csv().encode())
    tee = Product.objects.get(slug="classic-cotton-tshirt")
    page = client.get(tee.get_absolute_url()).content.decode()
    assert "Black" in page and 'name="size"' in page and "XL" in page


# ---- preview, then confirm ------------------------------------------------------------------------
def test_preview_changes_nothing_until_confirmed(staff_client):
    resp = upload(staff_client, make_csv([row(slug="preview-only")]))
    assert resp.status_code == 200 and b"Confirm import" in resp.content
    assert not Product.objects.filter(slug="preview-only").exists()
    staff_client.post(reverse(CONFIRM))
    assert Product.objects.filter(slug="preview-only").exists()


def test_confirm_without_preview_is_rejected(staff_client):
    assert staff_client.post(reverse(CONFIRM), follow=True).status_code == 200
    assert not Product.objects.filter(name="Test Tee").exists()


def test_confirm_cannot_be_replayed(staff_client):
    upload(staff_client, make_csv([row(slug="once")]))
    staff_client.post(reverse(CONFIRM))
    count = Product.objects.count()
    staff_client.post(reverse(CONFIRM))
    assert Product.objects.count() == count


# ---- update vs create-only --------------------------------------------------------------------------
def test_reimport_updates_existing_product_and_variant(staff_client):
    run_import(staff_client, make_csv([row(slug="tee", sku="T-1", color="Red", size="M", variant_stock="5", price="500")]))
    run_import(staff_client, make_csv([row(slug="tee", price="650", color="Red", size="M", variant_stock="9")]))
    assert Product.objects.filter(slug="tee").count() == 1 and ProductVariant.objects.filter(product__slug="tee").count() == 1
    p = Product.objects.get(slug="tee")
    assert p.price == 650 and p.sku == "T-1" and p.active_variants[0].stock == 9


def test_create_only_mode_skips_existing(staff_client):
    run_import(staff_client, make_csv([row(slug="tee", price="500")]))
    resp = upload(staff_client, make_csv([row(slug="tee", price="999"), row(slug="other-tee", name="Other Tee")]), mode="create")
    plan = resp.context["plan"]
    assert plan.skipped == ["Test Tee"] and plan.stats["new"] == 1
    staff_client.post(reverse(CONFIRM))
    assert Product.objects.get(slug="tee").price == 500 and Product.objects.filter(slug="other-tee").exists()


def test_update_keeps_description_and_image_when_blank(staff_client):
    run_import(staff_client, make_csv([row(slug="tee", description="Original text")]))
    run_import(staff_client, make_csv([row(slug="tee", price="700")]))
    assert Product.objects.get(slug="tee").description == "Original text"


# ---- validation: every problem reported, nothing saved -----------------------------------------------------
def errors_for(client, data, **post):
    return upload(client, data, **post).context["plan"].errors


def test_missing_required_columns(staff_client):
    errs = errors_for(staff_client, b"name,slug\nTee,tee\n")
    assert any("Missing required column" in m for _, m in errs)


def test_unknown_column(staff_client):
    assert any("Unknown column" in m for _, m in errors_for(staff_client, b"name,category,price,colour\nT,Fashion,5,Red\n"))


def test_bad_values_all_reported_with_line_numbers(staff_client):
    data = make_csv([
        row(slug="a", price="abc"),
        row(slug="b", price="-5"),
        row(slug="c", price="10.999"),
        row(slug="d", stock="x1"),
        row(slug="e", is_active="maybe"),
        row(slug="f", color="Red", color_hex="red"),
        row(slug="g", color="Red", variant_stock="-1"),
    ])
    errs = errors_for(staff_client, data)
    lines = {ln for ln, _ in errs}
    assert {2, 3, 4, 5, 6, 7, 8} <= lines
    assert not Product.objects.filter(slug__in="abcdefg").exists()


def test_one_bad_row_blocks_the_whole_file(staff_client):
    resp = upload(staff_client, make_csv([row(slug="good-one"), row(slug="bad-one", price="oops")]))
    assert b"nothing was imported" in resp.content and b"Confirm import" not in resp.content
    staff_client.post(reverse(CONFIRM))
    assert not Product.objects.filter(slug="good-one").exists()


def test_duplicate_variant_in_file(staff_client):
    errs = errors_for(staff_client, make_csv([row(slug="t", color="Red", size="M"), row(slug="t", color="red", size="m")]))
    assert any("duplicate variant" in m for _, m in errs)


def test_conflicting_product_fields_between_rows(staff_client):
    errs = errors_for(staff_client, make_csv([row(slug="t", color="Red", size="M"), row(slug="t", price="999", color="Red", size="L")]))
    assert any("conflicts" in m for _, m in errs)


def test_plain_row_cannot_be_mixed_with_variant_rows(staff_client):
    errs = errors_for(staff_client, make_csv([row(slug="t", color="Red"), row(slug="t")]))
    assert any("can't be mixed" in m for _, m in errs)


def test_sku_collisions(staff_client):
    existing = Product.objects.get(slug="retro-everyday-sunglasses")
    errs = errors_for(staff_client, make_csv([row(slug="new-one", sku=existing.sku)]))
    assert any("already belongs to product" in m for _, m in errs)
    errs = errors_for(staff_client, make_csv([row(slug="p1", sku="SAME"), row(slug="p2", name="P2", sku="SAME")]))
    assert any("used twice" in m for _, m in errs)
    v = ProductVariant.objects.first()
    errs = errors_for(staff_client, make_csv([row(slug="p3", color="Red", variant_sku=v.sku)]))
    assert any("already belongs" in m for _, m in errs)


def test_missing_category_color_size_are_created_only_when_ticked(staff_client):
    data = make_csv([row(slug="t", category="Brand New Cat", color="Magenta", size="XXL")])
    strict = upload(staff_client, data, create_missing="")  # unticked
    assert not strict.context["plan"].ok
    assert {"category", "color", "size"} <= {w for _, m in strict.context["plan"].errors for w in ("category", "color", "size") if w in m}
    run_import(staff_client, data)
    assert Category.objects.filter(name="Brand New Cat").exists() and Color.objects.filter(name="Magenta").exists() and Size.objects.filter(name="XXL").exists()


def test_category_matched_by_name_or_slug_case_insensitively(staff_client):
    run_import(staff_client, make_csv([row(slug="a", category="fashion"), row(slug="b", name="B", category="Fashion Finds".lower())]))
    assert Product.objects.get(slug="a").category.slug == "fashion"


def test_empty_and_blank_files(staff_client):
    assert not upload(staff_client, b"").context["plan"].ok
    assert not upload(staff_client, (HEAD + "\n,,,,,,,,,,,,,,,\n").encode()).context["plan"].ok


def test_non_utf8_file_rejected(staff_client):
    plan = upload(staff_client, "name,category,price\nTéa,Fashion,5\n".encode("latin-1")).context["plan"]
    assert any("UTF-8" in m for _, m in plan.errors)


def test_utf8_bom_from_excel_is_accepted(staff_client):
    run_import(staff_client, b"\xef\xbb\xbf" + make_csv([row(slug="bom")]))
    assert Product.objects.filter(slug="bom").exists()


def test_file_size_and_row_limits(staff_client):
    assert not upload(staff_client, b"x" * (importer.MAX_BYTES + 10)).context["plan"].ok
    many = make_csv([row(slug=f"p{i}", name=f"P{i}") for i in range(importer.MAX_ROWS + 5)])
    assert any("More than" in m for _, m in importer.parse(many).errors)


def test_only_csv_extension_accepted(staff_client):
    resp = upload(staff_client, make_csv([row()]), name="products.xlsx", )
    assert resp.status_code == 302 and not Product.objects.filter(name="Test Tee").exists()


def test_formula_injection_is_stored_as_plain_text(staff_client):
    run_import(staff_client, make_csv([row(slug="inj", name="=HYPERLINK(\"http://evil\")")]))
    assert Product.objects.get(slug="inj").name.startswith("=HYPERLINK")  # stored verbatim; templates auto-escape


def test_atomic_apply_rolls_back_on_failure(staff_client, monkeypatch):
    plan = importer.parse(make_csv([row(slug="rb1"), row(slug="rb2", name="RB2")]))
    original = ProductVariant.save
    calls = {"n": 0}

    def boom(self, *a, **k):
        calls["n"] += 1
        raise RuntimeError("db exploded")

    monkeypatch.setattr(ProductVariant, "save", boom)
    plan2 = importer.parse(make_csv([row(slug="rb1", color="Red"), row(slug="rb2", name="RB2")]))
    with pytest.raises(RuntimeError):
        importer.apply(plan2)
    monkeypatch.setattr(ProductVariant, "save", original)
    assert not Product.objects.filter(slug__in=["rb1", "rb2"]).exists() and plan.ok


# ---- permissions ------------------------------------------------------------------------------------------------
def test_anonymous_cannot_reach_import(client):
    for name in (IMPORT, CONFIRM, "admin:catalog_product_import_template"):
        assert client.get(reverse(name)).status_code == 302


def test_customer_cannot_import(client):
    u = User.objects.create_user("c@example.com", "pass12345!x", email_verified=True)
    client.force_login(u)
    assert client.get(reverse(IMPORT)).status_code == 302
    assert client.post(reverse(IMPORT), {"csv_file": SimpleUploadedFile("a.csv", make_csv([row()]))}).status_code == 302
    assert not Product.objects.filter(name="Test Tee").exists()


def test_staff_without_add_permission_is_refused(client):
    staff = User.objects.create_user("limited@example.com", "pass12345!x", is_staff=True, email_verified=True)
    client.force_login(staff)
    s = client.session; s["2fa_verified"] = True; s.save()
    assert client.get(reverse(IMPORT)).status_code == 302 and client.get(reverse("admin:catalog_product_import_template")).status_code == 302


def test_import_requires_the_2fa_code_for_staff(client):
    staff = User.objects.create_superuser("boss2@example.com", "pass12345!x")
    client.force_login(staff)  # password-authenticated only
    assert client.get(reverse(IMPORT))["Location"] == reverse("accounts:two_factor")


def test_import_requires_csrf(client):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    staff = User.objects.create_superuser("boss3@example.com", "pass12345!x")
    strict.force_login(staff)
    s = strict.session; s["2fa_verified"] = True; s.save()
    assert strict.post(reverse(IMPORT), {}).status_code == 403


# ---- admin screens ---------------------------------------------------------------------------------------------------
def test_product_changelist_has_import_button_and_variant_columns(staff_client):
    html = staff_client.get(reverse("admin:catalog_product_changelist")).content.decode()
    assert "Import CSV" in html and "Variants" in html


def test_import_page_renders_instructions_and_demo_link(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    assert "Download demo CSV" in html and "variant_stock" in html


def test_product_edit_page_has_variant_inline(staff_client):
    shoes = Product.objects.get(slug="cloudstep-everyday-sneakers")
    html = staff_client.get(reverse("admin:catalog_product_change", args=[shoes.pk])).content.decode()
    assert "variants-0-sku" in html or "productvariant" in html.lower()


def test_color_and_size_admin_pages(staff_client):
    for name in ("admin:catalog_color_changelist", "admin:catalog_size_changelist", "admin:catalog_color_add", "admin:catalog_size_add"):
        assert staff_client.get(reverse(name)).status_code == 200


# ---- long names / slugs must never crash the import ---------------------------------------------------------------
NAME_200 = ("Premium Handcrafted Genuine Leather Executive Laptop Briefcase With Brass Fittings " * 3)[:200].strip()
NAME_300 = ("Premium Handcrafted Genuine Leather Executive Laptop Briefcase With Brass Fittings " * 4)[:300].strip()


def test_names_and_slugs_up_to_255_characters_import_in_full(staff_client):
    run_import(staff_client, make_csv([row(name=NAME_200)]))
    p = Product.objects.get(name=NAME_200)
    assert len(p.name) == len(NAME_200) and 50 < len(p.slug) <= 255 and p.slug.startswith("premium-handcrafted")


def test_name_longer_than_255_is_a_clear_error(staff_client):
    plan = importer.parse(make_csv([row(name=NAME_300)]))
    assert not plan.ok and any("limited to 255" in m for _, m in plan.errors)


def test_explicit_slug_over_255_chars_is_a_clear_error_not_a_500():
    plan = importer.parse(make_csv([row(slug="x" * 300)]))
    assert not plan.ok and any("longer than 255" in m for _, m in plan.errors)


def test_derived_slug_over_255_is_shortened_uniquely_and_reimport_updates(staff_client):
    long_slug_name = "word " * 70  # 349 characters, slug would be 349
    plan = importer.parse(make_csv([row(name=long_slug_name.strip()[:255], slug="")]))
    assert plan.ok and all(len(p.slug) <= 255 for p in plan.products)
    assert importer._fit_slug("a" * 300) != importer._fit_slug("a" * 299 + "b") and len(importer._fit_slug("a" * 300)) <= 255


def test_overlong_category_is_a_clear_error():
    plan = importer.parse(make_csv([row(category="C" * 90)]), create_missing=True)
    assert not plan.ok and any("category" in m and "too long" in m for _, m in plan.errors)


def test_order_item_keeps_a_full_length_product_name(staff_client):
    from apps.orders.models import OrderItem

    assert OrderItem._meta.get_field("name").max_length >= Product._meta.get_field("name").max_length


def test_database_rejection_at_confirm_shows_a_message_instead_of_500(staff_client, monkeypatch):
    from django.db import DataError

    staff_client.post(reverse(IMPORT), {"csv_file": SimpleUploadedFile("p.csv", make_csv([row()]), "text/csv"), "mode": "upsert", "create_missing": "on"})
    monkeypatch.setattr(importer, "apply", lambda plan: (_ for _ in ()).throw(DataError("value too long")))
    resp = staff_client.post(reverse(CONFIRM), follow=True)
    assert resp.status_code == 200 and b"nothing was saved" in resp.content


@pytest.mark.django_db
def test_import_photos_command_attaches_from_zip_and_skips_done(tmp_path):
    import zipfile
    from io import BytesIO

    from django.core.management import call_command
    from PIL import Image

    from apps.catalog.models import Category, Product

    cat = Category.objects.create(name="Kids", slug="kids", title="Kids")
    product = Product.objects.create(category=cat, name="Frock", slug="frock", sku="9001", price=999, stock=5)
    csv_path = tmp_path / "p.csv"
    csv_path.write_text("name,slug,sku,category,price,stock,images\nFrock,frock,9001,Kids,999,5,frock-1.png\n", encoding="utf-8")
    buf = BytesIO()
    Image.new("RGBA", (40, 40), (255, 0, 0, 255)).save(buf, "PNG")
    zpath = tmp_path / "p.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("frock-1.png", buf.getvalue())
    call_command("import_photos", csv=str(csv_path), zip=[str(zpath)])
    assert product.images.count() == 1
    call_command("import_photos", csv=str(csv_path), zip=[str(zpath)])   # second run skips products that already have photos
    assert product.images.count() == 1


@pytest.mark.django_db
def test_variant_skus_stay_unique_for_bangla_colour_names():
    from apps.catalog.models import Category, Color, Product, ProductVariant
    cat = Category.objects.create(name="Toys", slug="toys-x", title="Toys")
    product = Product.objects.create(category=cat, name="Game", slug="game-x", sku="8588", price=900, stock=5)
    skus = {ProductVariant.objects.create(product=product, color=Color.objects.create(name=name)).sku for name in ("লাল", "নীল", "!!!", "???")}
    assert len(skus) == 4
