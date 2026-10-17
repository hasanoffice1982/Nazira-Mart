# ruff: noqa: F811
import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.catalog import image_import, importer
from apps.catalog.models import ImageImportJob, Product, ProductImage
from tests.test_csv_import import CONFIRM, IMPORT, make_csv, row, seeded, staff_client  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated(settings, tmp_path):
    settings.IMAGE_IMPORT_ASYNC = False
    settings.MEDIA_ROOT = str(tmp_path / "media")
    settings.IMPORT_TMP_DIR = str(tmp_path / "import_tmp")


def jpeg(color="red", size=(1800, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def make_zip(files: dict[str, bytes]) -> SimpleUploadedFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return SimpleUploadedFile("photos.zip", buf.getvalue(), "application/zip")


def run(client, rows, zip_file=None, **extra):
    data = {"csv_file": SimpleUploadedFile("p.csv", make_csv(rows), "text/csv"), "mode": "upsert", "create_missing": "on"}
    if zip_file is not None:
        data["photos_zip"] = zip_file
    r = client.post(reverse(IMPORT), data)
    assert r.status_code == 200
    plan = r.context["plan"]
    return r, plan


def test_zip_photos_attach_shared_and_per_color(staff_client):
    zf = make_zip({"tee-1.jpg": jpeg(), "photos/tee-2.JPG": jpeg("blue"), "tee-black.jpg": jpeg("black")})
    rows = [
        row(slug="tee", images="tee-1.jpg|tee-2.jpg", color="Black", color_hex="#000000", size="M", variant_stock="5", color_images="tee-black.jpg"),
        row(slug="tee", color="Black", size="L", variant_stock="5"),
    ]
    _, plan = run(staff_client, rows, zf)
    assert plan.ok and plan.stats["images"] == 3
    resp = staff_client.post(reverse(CONFIRM))
    assert resp.status_code == 302 and "photos" in resp.url
    job = ImageImportJob.objects.get()
    assert job.status == "done" and job.done == 1 and not job.errors
    p = Product.objects.get(slug="tee")
    shared = p.images.filter(color__isnull=True)
    assert shared.count() == 2 and p.images.filter(color__name="Black").count() == 1
    assert all(i.image.name.endswith(".webp") for i in p.images.all())
    assert not list(image_import.tmp_dir().glob("*.zip"))  # temp zip removed


def test_reimport_replaces_instead_of_duplicating(staff_client):
    for _ in range(2):
        run(staff_client, [row(slug="tee2", images="a.jpg")], make_zip({"a.jpg": jpeg()}))
        staff_client.post(reverse(CONFIRM))
    assert ProductImage.objects.filter(product__slug="tee2").count() == 1






def test_color_images_requires_color(staff_client):
    _, plan = run(staff_client, [row(slug="z", color_images="a.jpg")], make_zip({"a.jpg": jpeg()}))
    assert any("needs a color" in m for _, m in plan.errors)


def test_bad_photo_keeps_products_and_reports(staff_client):
    run(staff_client, [row(slug="bad", images="fake.jpg")], make_zip({"fake.jpg": b"not an image"}))
    staff_client.post(reverse(CONFIRM))
    job = ImageImportJob.objects.get()
    assert Product.objects.filter(slug="bad").exists() and job.errors and "valid image" in job.errors[0]


def test_zip_slip_names_and_junk_are_ignored(tmp_path):
    path = tmp_path / "t.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../../evil.jpg", jpeg())
        zf.writestr("__MACOSX/._a.jpg", b"x")
        zf.writestr(".hidden.jpg", b"x")
        zf.writestr("ok/a.jpg", jpeg())
    bank = image_import.ImageBank(path)
    assert bank.lookup("a.jpg") and bank.lookup("evil.jpg")  # matched by name only; nothing is ever extracted by path
    assert bank.lookup(".hidden.jpg") is None and not any("__macosx" in k for k in bank.full)


def test_ambiguous_names_are_rejected(tmp_path):
    path = tmp_path / "t.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a/x.jpg", jpeg())
        zf.writestr("b/x.jpg", jpeg("blue"))
    bank = image_import.ImageBank(path)
    with pytest.raises(image_import.ImportImageError):
        bank.lookup("x.jpg")
    assert bank.lookup("a/x.jpg")


def test_non_zip_and_wrong_extension_rejected(staff_client):
    bad = SimpleUploadedFile("photos.zip", b"definitely not a zip")
    _, plan = run(staff_client, [row(slug="q", images="a.jpg")], bad)
    assert any("valid ZIP" in m for _, m in plan.errors)
    r = staff_client.post(reverse(IMPORT), {"csv_file": SimpleUploadedFile("p.csv", make_csv([row()]), "text/csv"), "photos_zip": SimpleUploadedFile("photos.rar", b"x")})
    assert r.status_code == 302


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.jpg", "http://localhost/a.jpg", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/a.jpg", "ftp://example.com/a.jpg", "file:///etc/passwd", "http://example.com:8080/a.jpg"])
def test_links_to_private_or_odd_targets_are_refused(url):
    with pytest.raises(image_import.ImportImageError):
        image_import.fetch_url(url)


def test_link_photo_is_downloaded(monkeypatch, staff_client):
    monkeypatch.setattr(image_import, "fetch_url", lambda u: jpeg())
    _, plan = run(staff_client, [row(slug="lnk", images="https://cdn.example.com/p.jpg")])
    assert plan.ok
    staff_client.post(reverse(CONFIRM))
    assert ProductImage.objects.filter(product__slug="lnk").count() == 1


def test_link_limit(staff_client):
    refs = "|".join(f"https://cdn.example.com/{i}.jpg" for i in range(image_import.MAX_URLS + 1))
    _, plan = run(staff_client, [row(slug="many", images=refs)])
    assert any("photo links" in m for _, m in plan.errors) or any("more than" in m for _, m in plan.errors)


def test_job_pages_need_permission(client, staff_client):
    run(staff_client, [row(slug="j", images="a.jpg")], make_zip({"a.jpg": jpeg()}))
    staff_client.post(reverse(CONFIRM))
    job = ImageImportJob.objects.get()
    r = staff_client.get(reverse("admin:catalog_product_import_job_status", args=[job.pk]))
    assert r.json()["status"] == "done"
    assert staff_client.get(reverse("admin:catalog_product_import_job", args=[job.pk])).status_code == 200
    client.logout()
    assert client.get(reverse("admin:catalog_product_import_job_status", args=[job.pk])).status_code in (302, 403, 404)


def test_demo_csv_still_has_empty_photo_columns():
    plan = importer.parse(importer.demo_csv().encode())
    assert plan.ok and plan.image_count == 0


def test_shipped_demo_csv_and_zip_import_cleanly(staff_client):
    from pathlib import Path

    base = Path("sample_data/import_demo")
    data = {"csv_file": SimpleUploadedFile("p.csv", (base / "products_with_photos.csv").read_bytes(), "text/csv"),
            "photos_zip": SimpleUploadedFile("photos.zip", (base / "photos.zip").read_bytes(), "application/zip"),
            "mode": "upsert", "create_missing": "on"}
    plan = staff_client.post(reverse(IMPORT), data).context["plan"]
    assert plan.ok, plan.errors
    assert plan.stats["new"] + plan.stats["updated"] == 8 and plan.stats["images"] >= 17
    staff_client.post(reverse(CONFIRM))
    job = ImageImportJob.objects.get()
    assert job.status == "done" and not job.errors
    assert Product.objects.get(slug="studio-wireless-headphones").images.count() == 2
    assert Product.objects.get(slug="everyday-classic-tote-bag").images.filter(color__name="Charcoal").count() == 1


# ---- chunked ZIP upload (so a ZIP of up to 500 MB works behind Cloudflare's 100 MB request limit) --------------------
CHUNK_URL = "admin:catalog_product_import_zip_chunk"


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(image_import, "CHUNK_SIZE", 1000)  # real value is 20 MB; tiny pieces keep tests fast


def post_chunk(client, upload_id, index, total, data):
    return client.post(reverse(CHUNK_URL), {"upload_id": upload_id, "index": index, "total": total, "chunk": SimpleUploadedFile("chunk", data)})


def zip_bytes(files):
    return make_zip(files).read()


def test_zip_sent_in_pieces_is_joined_and_imports(staff_client, small_chunks):
    raw = zip_bytes({"a.jpg": jpeg(size=(900, 700)), "b.jpg": jpeg("blue", size=(900, 700))})
    pieces = [raw[i:i + 1000] for i in range(0, len(raw), 1000)]
    assert len(pieces) >= 3
    uid = "a" * 32
    for i, piece in enumerate(pieces):
        r = post_chunk(staff_client, uid, i, len(pieces), piece)
        assert r.status_code == 200 and r.json()["done"] == (i == len(pieces) - 1)
    token = r.json()["token"]
    assert token == uid and image_import.zip_path_for(token).read_bytes() == raw
    data = {"csv_file": SimpleUploadedFile("p.csv", make_csv([row(slug="chunky", images="a.jpg|b.jpg")]), "text/csv"), "zip_token": token, "mode": "upsert", "create_missing": "on"}
    plan = staff_client.post(reverse(IMPORT), data).context["plan"]
    assert plan.ok and plan.stats["images"] == 2
    staff_client.post(reverse(CONFIRM))
    assert ProductImage.objects.filter(product__slug="chunky").count() == 2 and ImageImportJob.objects.get().status == "done"


def test_pieces_must_arrive_in_order_and_at_full_size(staff_client, small_chunks):
    uid = "b" * 32
    assert post_chunk(staff_client, uid, 1, 3, b"x" * 1000).status_code == 409          # piece 0 never arrived
    assert post_chunk(staff_client, uid, 0, 3, b"x" * 1000).status_code == 200
    assert post_chunk(staff_client, uid, 2, 3, b"x" * 10).status_code == 409             # piece 1 skipped
    assert post_chunk(staff_client, uid, 1, 3, b"x" * 500).status_code == 400            # a middle piece must be full size


def test_bad_ids_numbers_and_non_zip_are_rejected(staff_client, small_chunks):
    assert post_chunk(staff_client, "../../etc/passwd", 0, 1, b"x").status_code == 400
    assert post_chunk(staff_client, "c" * 32, 5, 2, b"x").status_code == 400
    assert post_chunk(staff_client, "c" * 32, 0, 999, b"x").status_code == 400
    r = post_chunk(staff_client, "d" * 32, 0, 1, b"this is not a zip")
    assert r.status_code == 400 and "valid ZIP" in r.json()["error"] and image_import.zip_path_for("d" * 32) is None


def test_total_size_limit_is_enforced(staff_client, small_chunks, monkeypatch):
    monkeypatch.setattr(image_import, "MAX_ZIP_BYTES", 1500)
    uid = "e" * 32
    assert post_chunk(staff_client, uid, 0, 3, b"x" * 1000).status_code == 200
    r = post_chunk(staff_client, uid, 1, 3, b"x" * 1000)
    assert r.status_code == 413 and not (image_import.tmp_dir() / f"{uid}.part").exists()


def test_only_import_staff_can_send_pieces(client, small_chunks):
    assert post_chunk(client, "f" * 32, 0, 1, b"x").status_code in (302, 403)
    assert image_import.zip_path_for("f" * 32) is None


def test_token_cannot_reach_other_files(staff_client):
    assert image_import.zip_path_for("../../etc/passwd") is None and image_import.zip_path_for("") is None
    assert image_import.MAX_ZIP_BYTES == 500 * 1024 * 1024 and image_import.CHUNK_SIZE == 20 * 1024 * 1024


def test_import_page_has_the_chunk_uploader(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    assert 'name="zip_token"' in html and "zip-chunk" in html and "500 MB" in html and "5 MB each" in html




# ---- commas between photos, and photos split over several ZIP parts ---------------------------------------------------
@pytest.mark.parametrize("cell,expected", [
    ("a.png,b.png", ["a.png", "b.png"]),
    ("a.png, b.png ,c.jpg", ["a.png", "b.png", "c.jpg"]),
    ("a.png|b.png", ["a.png", "b.png"]),
    ("a.png;b.png\nc.png", ["a.png", "b.png", "c.png"]),
    ("a.png,A.PNG", ["a.png"]),  # duplicates ignored
    ("https://cdn.example.com/p.jpg?x=1,2,b.png", ["https://cdn.example.com/p.jpg?x=1,2", "b.png"]),
    ("https://x.com/a.jpg, https://x.com/b.jpg", ["https://x.com/a.jpg", "https://x.com/b.jpg"]),
    ("", []),
    ("bgm-1001-super-soft-cotton-fleece-baby-hoodie-warm-and-skin-pink-1.png,bgm-1001-super-soft-cotton-fleece-baby-hoodie-warm-and-skin-pink-2.png",
     ["bgm-1001-super-soft-cotton-fleece-baby-hoodie-warm-and-skin-pink-1.png", "bgm-1001-super-soft-cotton-fleece-baby-hoodie-warm-and-skin-pink-2.png"]),
])
def test_photo_cells_accept_commas_and_other_separators(cell, expected):
    assert image_import.split_refs(cell) == expected


def save_zip(tmp, name, files):
    path = tmp / name
    with zipfile.ZipFile(path, "w") as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return path


def test_photos_spread_over_three_zip_parts_import_together(staff_client, tmp_path):
    parts = [
        save_zip(tmp_path, "p1.zip", {"hoodie-1.png": jpeg(), "hoodie-2.png": jpeg("blue")}),
        save_zip(tmp_path, "p2.zip", {"saree-1.png": jpeg("green")}),
        save_zip(tmp_path, "p3.zip", {"bag-1.jpg": jpeg("black")}),
    ]
    tokens = []
    for i, part in enumerate(parts):
        token = f"{i:032x}"
        (image_import.tmp_dir() / f"{token}.zip").write_bytes(part.read_bytes())
        tokens.append(token)
    rows = [row(slug="hoodie", name="Hoodie", images="hoodie-1.png,hoodie-2.png"), row(slug="saree", name="Saree", images="saree-1.png"), row(slug="bag", name="Bag", images="bag-1.jpg")]
    data = {"csv_file": SimpleUploadedFile("p.csv", make_csv(rows), "text/csv"), "zip_token": ",".join(tokens), "mode": "upsert", "create_missing": "on"}
    plan = staff_client.post(reverse(IMPORT), data).context["plan"]
    assert plan.ok and plan.stats["images"] == 4
    staff_client.post(reverse(CONFIRM))
    job = ImageImportJob.objects.get()
    assert job.status == "done" and not job.errors
    assert [ProductImage.objects.filter(product__slug=s).count() for s in ("hoodie", "saree", "bag")] == [2, 1, 1]
    assert not list(image_import.tmp_dir().glob("*.zip"))  # every part cleaned up




def test_same_name_in_two_parts_is_ambiguous(tmp_path):
    a = save_zip(tmp_path, "a.zip", {"x.png": jpeg()})
    b = save_zip(tmp_path, "b.zip", {"x.png": jpeg("blue")})
    bank = image_import.ImageBank([a, b])
    with pytest.raises(image_import.ImportImageError):
        bank.lookup("x.png")


def test_too_many_zip_parts_are_refused(staff_client, tmp_path):
    tokens = []
    for i in range(image_import.MAX_ZIPS + 1):
        token = f"{i + 100:032x}"
        (image_import.tmp_dir() / f"{token}.zip").write_bytes(save_zip(tmp_path, f"{i}.zip", {f"{i}.png": jpeg()}).read_bytes())
        tokens.append(token)
    data = {"csv_file": SimpleUploadedFile("p.csv", make_csv([row(slug="y")]), "text/csv"), "zip_token": ",".join(tokens), "mode": "upsert", "create_missing": "on"}
    resp = staff_client.post(reverse(IMPORT), data, follow=True)
    assert b"at most" in resp.content and not list(image_import.tmp_dir().glob("*.zip"))


def test_import_page_accepts_several_zip_files(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    assert " multiple " in html and "data-max-files" in html and "ZIP parts" in html and "or a comma" in html


def test_csv_field_explains_how_photos_work(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    before_zip = html.split('id="photos_zip"')[0]
    assert "How photos work" in before_zip and "images</code> column" in before_zip and "color_images" in before_zip
    assert f"{image_import.max_per_product()} photos per product" in before_zip and "comma or <code>|</code>" in before_zip and "up to 5 MB" in before_zip


# ---- "add photos later" ---------------------------------------------------------------------------------------------


def test_bad_extension_is_still_an_error(staff_client):
    _, plan = run(staff_client, [row(slug="y", images="a.exe")], make_zip({"a.exe": b"x"}))
    assert any(".jpg" in m for _, m in plan.errors)


def test_uploader_script_disables_the_button_and_shows_progress():
    js = open("static/js/zip_uploader.js").read()
    assert "submit.disabled=true" in js and "Uploading photos" in js and "beforeunload" in js and "keep filling in the form" in js
    assert "dataset.chunkUrl" in js  # the address comes from the page


def test_import_page_loads_the_uploader_script_and_offers_photos_later(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    assert "js/zip_uploader" in html and "Upload photos only" in html and reverse("admin:catalog_product_photos_upload") in html
    assert "attached automatically" in html


# ---- photos can come later: names are saved, a later ZIP attaches them by name ------------------------------------------------
def csv_with_names(**over):
    return make_csv([row(slug="later", name="Later Tee", images="a.png, b.png", color="Red", color_hex="#ff0000", color_images="red.png", variant_stock="4", **over)])


def import_csv(client, csv_bytes, zip_file=None):
    data = {"csv_file": SimpleUploadedFile("p.csv", csv_bytes, "text/csv"), "mode": "upsert", "create_missing": "on"}
    if zip_file is not None:
        data["photos_zip"] = zip_file
    plan = client.post(reverse(IMPORT), data).context["plan"]
    assert plan.ok, plan.errors
    resp = client.post(reverse(CONFIRM))
    return plan, resp


def test_without_a_zip_names_are_saved_and_the_import_succeeds(staff_client):
    plan, resp = import_csv(staff_client, csv_with_names())
    assert plan.stats["images"] == 0 and plan.stats["pending"] == 3
    assert resp.status_code == 302 and "photos" not in resp.url and not ImageImportJob.objects.exists()
    p = Product.objects.get(slug="later")
    assert p.images.count() == 0
    assert sorted(p.pending_photos.values_list("ref", flat=True)) == ["a.png", "b.png", "red.png"]
    assert p.pending_photos.get(ref="red.png").color.name == "Red" and p.pending_photos.get(ref="a.png").color is None


def test_uploading_the_photos_later_attaches_them_by_name(staff_client):
    import_csv(staff_client, csv_with_names())
    zf = make_zip({"A.PNG": jpeg(), "photos/b.png": jpeg("blue"), "red.png": jpeg("red"), "stranger.png": jpeg("green")})
    resp = staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": zf}, follow=True)
    assert b"Matched 3 photos to 1 product" in resp.content and b"1 file in the ZIP matched no waiting name" in resp.content
    p = Product.objects.get(slug="later")
    assert p.images.filter(color__isnull=True).count() == 2 and p.images.filter(color__name="Red").count() == 1
    assert not p.pending_photos.exists() and ImageImportJob.objects.get().status == "done"
    assert [i.position for i in p.images.order_by("position")] == [0, 1, 2]


def test_later_upload_adds_to_existing_photos_instead_of_replacing(staff_client):
    plan, _ = import_csv(staff_client, make_csv([row(slug="mix", name="Mix", images="now.png,later.png")]), make_zip({"now.png": jpeg()}))
    assert plan.stats["images"] == 1 and plan.stats["pending"] == 1
    ImageImportJob.objects.all().update(status="done")
    p = Product.objects.get(slug="mix")
    assert p.images.count() == 1 and list(p.pending_photos.values_list("ref", flat=True)) == ["later.png"]
    staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"later.png": jpeg("blue")})})
    assert p.images.count() == 2 and not p.pending_photos.exists()


def test_photos_spread_over_several_uploads_attach_as_they_arrive(staff_client):
    import_csv(staff_client, make_csv([row(slug="multi", name="Multi", images="one.png,two.png")]))
    staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"one.png": jpeg()})})
    p = Product.objects.get(slug="multi")
    assert p.images.count() == 1 and list(p.pending_photos.values_list("ref", flat=True)) == ["two.png"]
    staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"two.png": jpeg("blue")})})
    assert p.images.count() == 2 and not p.pending_photos.exists()


def test_reimporting_the_csv_replaces_the_waiting_names_without_duplicates(staff_client):
    import_csv(staff_client, make_csv([row(slug="re", name="Re", images="a.png,b.png")]))
    import_csv(staff_client, make_csv([row(slug="re", name="Re", images="b.png,c.png")]))
    assert sorted(Product.objects.get(slug="re").pending_photos.values_list("ref", flat=True)) == ["b.png", "c.png"]


def test_csv_without_photo_columns_keeps_names_waiting_from_an_earlier_import(staff_client):
    import_csv(staff_client, make_csv([row(slug="keep", name="Keep", images="a.png")]))
    import_csv(staff_client, make_csv([row(slug="keep", name="Keep", price="900")]))
    assert Product.objects.get(slug="keep").pending_photos.count() == 1


def test_a_zip_with_nothing_matching_changes_nothing(staff_client):
    import_csv(staff_client, make_csv([row(slug="nm", name="NM", images="wanted.png")]))
    resp = staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"other.png": jpeg()})}, follow=True)
    assert b"None of the photos" in resp.content and b"other.png" in resp.content
    assert Product.objects.get(slug="nm").pending_photos.count() == 1 and not ImageImportJob.objects.exists()


def test_same_name_in_two_places_is_skipped_not_guessed(staff_client):
    import_csv(staff_client, make_csv([row(slug="amb", name="Amb", images="x.png")]))
    resp = staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"a/x.png": jpeg(), "b/x.png": jpeg("blue")})}, follow=True)
    assert b"None of the photos" in resp.content and Product.objects.get(slug="amb").pending_photos.count() == 1


def test_photos_page_lists_waiting_names_and_needs_permission(staff_client, client):
    import_csv(staff_client, make_csv([row(slug="list", name="List", images="zz-photo.png")]))
    page = staff_client.get(reverse("admin:catalog_product_photos_upload")).content.decode()
    assert "zz-photo.png" in page and "waiting for upload" in page and 'name="zip_token"' in page
    client.logout()
    assert client.get(reverse("admin:catalog_product_photos_upload")).status_code in (302, 403)


def test_product_page_in_admin_shows_waiting_photos(staff_client):
    import_csv(staff_client, make_csv([row(slug="inl", name="Inl", images="zz-wait.png")]))
    p = Product.objects.get(slug="inl")
    html = staff_client.get(reverse("admin:catalog_product_change", args=[p.pk])).content.decode()
    assert "zz-wait.png" in html and "waiting for upload" in html


def test_product_list_has_the_upload_photos_button(staff_client):
    html = staff_client.get(reverse("admin:catalog_product_changelist")).content.decode()
    assert reverse("admin:catalog_product_photos_upload") in html and "Upload photos" in html


def test_import_page_accepts_photo_zips_without_a_csv(staff_client):
    import_csv(staff_client, make_csv([row(slug="zonly", name="Zonly", images="solo.png")]))
    resp = staff_client.post(reverse(IMPORT), {"photos_zip": make_zip({"solo.png": jpeg()})}, follow=True)
    assert b"Matched 1 photo to 1 product" in resp.content
    assert Product.objects.get(slug="zonly").images.count() == 1 and not Product.objects.get(slug="zonly").pending_photos.exists()


def test_import_page_with_nothing_chosen_explains_what_to_pick(staff_client):
    resp = staff_client.post(reverse(IMPORT), {}, follow=True)
    assert b"only the photo ZIP" in resp.content


def test_csv_field_is_no_longer_required_on_the_import_page(staff_client):
    html = staff_client.get(reverse(IMPORT)).content.decode()
    csv_input = html.split('id="csv_file"')[1].split(">")[0]
    assert "required" not in csv_input and "Photos only?" in html


def test_zip_with_no_waiting_names_at_all_tells_you_to_import_the_csv_again(staff_client):
    resp = staff_client.post(reverse("admin:catalog_product_photos_upload"), {"photos_zip": make_zip({"a.png": jpeg()})}, follow=True)
    assert b"Import your CSV" in resp.content and b"choose the CSV and this ZIP together" in resp.content.replace(b"Or ", b"or ") or b"CSV and this ZIP together" in resp.content


def test_up_to_20_photos_per_product_are_allowed_and_21_is_not(staff_client):
    ok = ",".join(f"p{i}.png" for i in range(20))
    assert importer.parse(make_csv([row(slug="many30", images=ok)])).ok
    too_many = ",".join(f"p{i}.png" for i in range(21))
    plan = importer.parse(make_csv([row(slug="many31", images=too_many)]))
    assert not plan.ok and any("more than 20 photos" in m and "Site settings" in m for _, m in plan.errors)


def test_the_photo_limits_are_editable_in_site_settings(staff_client):
    from apps.core.models import SiteSettings

    s = SiteSettings.load_fresh()
    s.max_photos_per_product = 3
    s.bulk_upload_max_files = 2
    s.save()
    plan = importer.parse(make_csv([row(slug="few", images="a.png,b.png,c.png,d.png")]))
    assert not plan.ok and any("more than 3 photos" in m for _, m in plan.errors)
    assert importer.parse(make_csv([row(slug="few", images="a.png,b.png,c.png")])).ok
    assert "3 photos per product" in staff_client.get(reverse(IMPORT)).content.decode()
    s.max_photos_per_product = 500
    with pytest.raises(Exception):
        s.full_clean()
