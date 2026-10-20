import nested_admin
from adminsortable2.admin import SortableAdminMixin
from django.contrib import admin
from django.utils.html import format_html

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import path

from apps.core.images import rendition_url
from apps.core.models import SiteSettings
from apps.core.widgets import RichTextAdminMixin

import logging
import re
import uuid

from django.db import DataError, IntegrityError
from django.http import Http404, JsonResponse

from . import image_import, importer
from .models import Category, Color, ImageImportJob, PendingPhoto, Product, ProductImage, ProductVariant, Size, validate_photo

IMPORT_SESSION_KEY = "catalog_import"


class ProductInline(nested_admin.NestedTabularInline):
    model = Product
    extra = 0
    fields = ("name", "slug", "sku", "price", "old_price", "stock", "badge", "image", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    show_change_link = True


@admin.register(Category)
class CategoryAdmin(SortableAdminMixin, nested_admin.NestedModelAdmin):
    """Drag rows by the handle to set the order used on the home page tiles and in the header menu."""

    list_display = ("name", "slug", "product_count", "show_in_nav", "is_active")
    list_editable = ("show_in_nav", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductInline]

    @admin.display(description="Products")
    def product_count(self, obj):
        return obj.products.count()


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ("name", "swatch", "hex_code", "position")
    list_editable = ("hex_code", "position")
    search_fields = ("name",)

    @admin.display(description="Swatch")
    def swatch(self, obj):
        return format_html('<span style="display:inline-block;width:22px;height:22px;border-radius:50%;background:{};border:1px solid #cfdcd5"></span>', obj.hex_code)


@admin.register(Size)
class SizeAdmin(admin.ModelAdmin):
    list_display = ("name", "position")
    list_editable = ("position",)
    search_fields = ("name",)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ("color", "size", "sku", "price", "stock", "is_active", "image")
    autocomplete_fields = ("color", "size")
    show_change_link = False


class PendingPhotoInline(admin.TabularInline):
    model = PendingPhoto
    extra = 0
    fields = ("ref", "color", "position")
    readonly_fields = ("ref", "color", "position")
    verbose_name = "photo waiting for upload"
    verbose_name_plural = "Photos waiting for upload (attached automatically when a ZIP with these file names is uploaded)"

    def has_add_permission(self, request, obj=None):
        return False


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ("preview", "image", "color", "alt_text", "position")
    readonly_fields = ("preview",)
    autocomplete_fields = ("color",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.pk and obj.image:
            return format_html('<img src="{}" style="width:56px;height:56px;object-fit:cover;border-radius:6px">', rendition_url(obj.image, 160))
        return "—"


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        return [super(MultipleImageField, self).clean(f, initial) for f in files if f]


class BulkPhotoForm(forms.Form):
    photos = MultipleImageField(label="Photos", help_text="Select many files at once. JPEG, PNG or WebP, up to 5 MB each (100 MB per upload). Photos are compressed automatically.")
    color = forms.ModelChoiceField(queryset=Color.objects.all(), required=False, empty_label="— Shared (all colors) —", label="Show for color")
    alt_text = forms.CharField(max_length=160, required=False, label="Alt text (optional)", help_text="Describes the photo for screen readers; defaults to the product name.")


MAX_BULK_BYTES = 100 * 1024 * 1024   # Nginx and Cloudflare cap one request at about 100 MB


@admin.register(Product)
class ProductAdmin(RichTextAdminMixin, admin.ModelAdmin):
    rich_fields = ("description",)
    change_list_template = "admin/catalog/product/change_list.html"
    list_display = ("thumb", "name", "sku", "category", "ships_from", "price", "variant_count", "stock_total", "stock_flag", "is_gift", "is_popular_pick", "is_active")
    list_display_links = ("thumb", "name")
    list_filter = ("category", "is_active", "is_gift", "is_popular_pick")   # + "who ships it" is added by apps.dealers.admin
    list_editable = ("price", "is_gift", "is_popular_pick", "is_active")
    search_fields = ("name", "sku", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    list_select_related = ("category",)
    list_prefetch_related = ("variants",)
    inlines = [ProductImageInline, PendingPhotoInline, ProductVariantInline]
    change_form_template = "admin/catalog/product/change_form.html"
    fieldsets = (
        ("Basics", {"fields": ("name", "slug", "sku", "category", "description")}),
        ("Who ships it", {"fields": ("dealer",), "description": "Choose a dealer if one of your own dealers ships this product (orders go to their automation). Leave empty for dropship-supplier products (linked by the API) and for stock you keep yourself."}),
        ("Pricing & stock", {"fields": ("price", "old_price", "badge", "stock", "is_active"), "description": "Stock here applies only to products without variants. With variants, stock is managed per variant below."}),
        ("Collections", {"fields": ("is_gift",), "description": "Tick to list this product under Shop All > Gift ideas. New arrivals (newest first) and Discounted (old price above price) fill themselves."}),
        ("Home page: Popular products", {"fields": ("is_popular_pick", "popular_order"), "description": "Pin this product to the home page Popular products section. Products that sell well are added there automatically."}),
        ("Media & look", {"fields": ("image", "art_type", "bg_color", "swatches", "rating"), "description": "Gallery photos (added in the Photos tab or with Upload many photos) take priority over the single photo below."}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category", "dealer", "supplier_link").prefetch_related("variants", "images")

    @admin.display(description="Ships from")
    def ships_from(self, obj):
        kind, label = obj.fulfilment
        css = {"dropship": "badge-info", "dealer": "badge-warning", "own": "badge-secondary"}[kind]
        return format_html('<span class="badge {}">{}</span>', css, label)

    @admin.display(description="")
    def thumb(self, obj):
        cover = obj.cover_image
        if cover:
            return format_html('<img src="{}" style="width:40px;height:40px;object-fit:cover;border-radius:6px">', rendition_url(cover, 160))
        return "—"

    @admin.display(description="Variants")
    def variant_count(self, obj):
        return len(obj.active_variants) or "—"

    @admin.display(description="Stock")
    def stock_total(self, obj):
        return obj.total_stock

    @admin.display(description="Level")
    def stock_flag(self, obj):
        stock = obj.total_stock
        css, text = ("status status-cancelled", "Out") if stock == 0 else ("status status-pending", "Low") if stock <= 5 else ("status", "OK")
        return format_html('<span class="{}">{}</span>', css, text)

    # ---- CSV import ----------------------------------------------------------------
    def get_urls(self):
        wrap = self.admin_site.admin_view
        return [
            path("<int:object_id>/photos/", wrap(self.bulk_photos_view), name="catalog_product_photos"),
            path("import/", wrap(self.import_view), name="catalog_product_import"),
            path("import/confirm/", wrap(self.import_confirm_view), name="catalog_product_import_confirm"),
            path("import/template.csv", wrap(self.import_template_view), name="catalog_product_import_template"),
            path("import/zip-chunk/", wrap(self.import_zip_chunk_view), name="catalog_product_import_zip_chunk"),
            path("photos-upload/", wrap(self.photos_upload_view), name="catalog_product_photos_upload"),
            path("import/photos/<uuid:job_id>/", wrap(self.import_job_view), name="catalog_product_import_job"),
            path("import/photos/<uuid:job_id>/status/", wrap(self.import_job_status_view), name="catalog_product_import_job_status"),
        ] + super().get_urls()

    def _can_import(self, request) -> bool:
        return self.has_add_permission(request) and self.has_change_permission(request)

    def _import_context(self, request, **extra):
        return {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": "Import products from CSV",
            "columns": importer.COLUMNS, "max_kb": importer.MAX_BYTES // 1000, "max_rows": importer.MAX_ROWS, "max_zip_mb": image_import.MAX_ZIP_BYTES // (1024 * 1024), "max_zip_bytes": image_import.MAX_ZIP_BYTES, "max_total_bytes": image_import.MAX_TOTAL_ZIP_BYTES, "max_total_mb": image_import.MAX_TOTAL_ZIP_BYTES // (1024 * 1024), "max_zips": image_import.MAX_ZIPS, "max_per_product": image_import.max_per_product(), "max_links": image_import.MAX_URLS, "max_refs": image_import.MAX_REFS, "chunk_size": image_import.CHUNK_SIZE, **extra,
        }

    def import_template_view(self, request):
        if not self._can_import(request):
            return redirect("admin:catalog_product_changelist")
        resp = HttpResponse(importer.demo_csv(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = 'attachment; filename="narizmart_products_demo.csv"'
        return resp

    def _collect_zip_paths(self, request) -> list[str] | None:
        """ZIPs sent for this request: tokens from the page's chunked uploader, or plain files (up to 100 MB each). None = an error was shown."""
        zip_paths: list[str] = []
        for token in re.split(r"[,\s]+", request.POST.get("zip_token", "")):
            found = image_import.zip_path_for(token)
            if found is not None and str(found) not in zip_paths:
                zip_paths.append(str(found))
        if not zip_paths:
            for zip_upload in request.FILES.getlist("photos_zip"):
                if not zip_upload.name.lower().endswith(".zip"):
                    messages.error(request, "The photos file must be a .zip.")
                    return None
                if zip_upload.size > 100 * 1024 * 1024:
                    messages.error(request, "Your browser must run the page's script to send a ZIP over 100 MB. Reload the page and choose the file again.")
                    return None
                image_import.purge_old_uploads()
                target = image_import.tmp_dir() / f"{uuid.uuid4().hex}.zip"
                with open(target, "wb") as out:
                    for chunk in zip_upload.chunks():
                        out.write(chunk)
                zip_paths.append(str(target))
        if len(zip_paths) > image_import.MAX_ZIPS or sum(image_import.Path(p).stat().st_size for p in zip_paths) > image_import.MAX_TOTAL_ZIP_BYTES:
            image_import.drop_paths(zip_paths)
            messages.error(request, f"Use at most {image_import.MAX_ZIPS} ZIP files, {image_import.MAX_TOTAL_ZIP_BYTES // (1024 * 1024)} MB in total.")
            return None
        return zip_paths

    def photos_upload_view(self, request):
        """Upload photos on their own: files are matched by name to the photo names saved from earlier CSV imports."""
        if not self._can_import(request):
            messages.error(request, "You don't have permission to import photos.")
            return redirect("admin:catalog_product_changelist")
        waiting = PendingPhoto.objects.select_related("product")
        context = self._import_context(
            request, title="Upload photos", waiting_count=waiting.count(), waiting_products=waiting.values("product_id").distinct().count(),
            waiting_names=list(waiting.order_by("product__name", "position").values_list("ref", flat=True)[:40]),
        )
        if request.method != "POST":
            return render(request, "admin/catalog/product/import_photos.html", context)
        zip_paths = self._collect_zip_paths(request)
        if zip_paths is None:
            return redirect("admin:catalog_product_photos_upload")
        if not zip_paths:
            messages.error(request, "Choose the photo ZIP file(s) first.")
            return redirect("admin:catalog_product_photos_upload")
        bank = image_import.ImageBank(zip_paths)
        if bank.error:
            image_import.drop_paths(zip_paths)
            messages.error(request, bank.error)
            return redirect("admin:catalog_product_photos_upload")
        entries, matched, unmatched, ambiguous = image_import.match_pending(bank)
        if not entries:
            image_import.drop_paths(zip_paths)
            if not PendingPhoto.objects.exists():
                messages.warning(request, "No photo names are waiting for upload, so there is nothing to match these files to. Import your CSV (with the images column) once more: products are updated, not duplicated, and its photo names are saved. Then upload this ZIP again. Or choose the CSV and this ZIP together to attach the photos in one step.")
            else:
                hint = f" Files in your ZIP: {', '.join(unmatched[:8])}{'…' if len(unmatched) > 8 else ''}." if unmatched else ""
                messages.warning(request, "None of the photos in this ZIP match a photo name waiting for upload." + hint)
            return redirect("admin:catalog_product_photos_upload")
        note = f"Matched {matched} photo{'s' if matched != 1 else ''} to {len(entries)} product{'s' if len(entries) != 1 else ''}."
        if unmatched:
            note += f" {len(unmatched)} file{'s' if len(unmatched) != 1 else ''} in the ZIP matched no waiting name and {'were' if len(unmatched) != 1 else 'was'} ignored."
        if ambiguous:
            note += f" Skipped (same name in more than one place): {', '.join(ambiguous[:5])}."
        messages.success(request, note)
        job = ImageImportJob.objects.create(plan=entries, zip_path="\n".join(zip_paths), total=len(entries))
        image_import.start_job(job)
        return redirect("admin:catalog_product_import_job", job.pk)

    def import_zip_chunk_view(self, request):
        """One piece of a big photo ZIP (the browser sends 20 MB at a time; Cloudflare rejects bigger requests)."""
        if request.method != "POST" or not self._can_import(request):
            return JsonResponse({"error": "Not allowed."}, status=403)
        chunk = request.FILES.get("chunk")
        try:
            if chunk is None:
                raise image_import.ChunkError("No data.")
            token = image_import.receive_chunk(request.POST.get("upload_id", ""), int(request.POST.get("index", "-1")), int(request.POST.get("total", "0")), chunk)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Bad request."}, status=400)
        except image_import.ChunkError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)
        return JsonResponse({"done": token is not None, "token": token})

    def import_view(self, request):
        if not self._can_import(request):
            messages.error(request, "You don't have permission to import products.")
            return redirect("admin:catalog_product_changelist")
        if request.method != "POST":
            return render(request, "admin/catalog/product/import.html", self._import_context(request, mode="upsert", create_missing=True))
        upload = request.FILES.get("csv_file")
        mode = request.POST.get("mode", "upsert")
        create_missing = bool(request.POST.get("create_missing"))
        if upload is None:
            if request.POST.get("zip_token") or request.FILES.getlist("photos_zip"):
                return self.photos_upload_view(request)  # no CSV: the ZIP(s) are matched to photo names saved from earlier imports
            messages.error(request, "Choose a CSV file, or only the photo ZIP(s) to attach photos to products already imported.")
            return redirect("admin:catalog_product_import")
        if not upload.name.lower().endswith(".csv"):
            messages.error(request, "Only .csv files are accepted.")
            return redirect("admin:catalog_product_import")
        raw = upload.read(importer.MAX_BYTES + 1)
        self._drop_pending_zip(request)
        zip_paths = self._collect_zip_paths(request)
        if zip_paths is None:
            return redirect("admin:catalog_product_import")
        zip_path = "\n".join(zip_paths)
        image_import.purge_old_uploads()
        plan = importer.parse(raw, mode=mode, create_missing=create_missing, bank=image_import.ImageBank(zip_paths))
        if plan.ok:
            request.session[IMPORT_SESSION_KEY] = {"csv": raw.decode("utf-8-sig"), "mode": mode, "create_missing": create_missing, "zip": zip_path}
        else:
            request.session.pop(IMPORT_SESSION_KEY, None)
            image_import.drop_paths(zip_path)
        return render(request, "admin/catalog/product/import.html", self._import_context(request, plan=plan, mode=mode, create_missing=create_missing, filename=upload.name))

    @staticmethod
    def _drop_pending_zip(request) -> None:
        old = request.session.pop(IMPORT_SESSION_KEY, None)
        if old and old.get("zip"):
            image_import.drop_paths(old["zip"])

    def import_confirm_view(self, request):
        if request.method != "POST" or not self._can_import(request):
            return redirect("admin:catalog_product_import")
        saved = request.session.pop(IMPORT_SESSION_KEY, None)
        if not saved:
            messages.error(request, "That import expired. Upload the file again.")
            return redirect("admin:catalog_product_import")
        zip_path = saved.get("zip", "")
        bank = image_import.ImageBank([p for p in image_import.paths_of(zip_path) if image_import.Path(p).exists()])
        plan = importer.parse(saved["csv"].encode(), mode=saved["mode"], create_missing=saved["create_missing"], bank=bank)  # re-validate against current data
        if not plan.ok:
            image_import.drop_paths(zip_path)
            messages.error(request, "The catalog changed since the preview and the file no longer validates. Review and upload again.")
            return render(request, "admin/catalog/product/import.html", self._import_context(request, plan=plan, mode=saved["mode"], create_missing=saved["create_missing"]))
        try:
            result = importer.apply(plan)
        except (DataError, IntegrityError) as exc:  # the import is one transaction, so nothing was saved
            logging.getLogger("apps.catalog").exception("product import failed")
            messages.error(request, f"The import was stopped and nothing was saved: the database rejected a value ({exc.__class__.__name__}). Check for very long or duplicate values and try again.")
            image_import.drop_paths(zip_path)
            return redirect("admin:catalog_product_import")
        messages.success(
            request,
            f"Imported: {result['created']} product(s) created, {result['updated']} updated; "
            f"{result['variants_created']} variant(s) created, {result['variants_updated']} updated.",
        )
        entries = plan.image_entries
        if not entries:
            image_import.drop_paths(zip_path)
            return redirect("admin:catalog_product_changelist")
        job = ImageImportJob.objects.create(plan=entries, zip_path=zip_path, total=len(entries))
        image_import.start_job(job)
        return redirect("admin:catalog_product_import_job", job.pk)

    def _job_or_404(self, request, job_id):
        if not self._can_import(request):
            raise Http404
        return ImageImportJob.objects.filter(pk=job_id).first() or self._missing()

    @staticmethod
    def _missing():
        raise Http404

    def import_job_view(self, request, job_id):
        job = self._job_or_404(request, job_id)
        return render(request, "admin/catalog/product/import_job.html", self._import_context(request, job=job, title="Photo import"))

    def import_job_status_view(self, request, job_id):
        job = self._job_or_404(request, job_id)
        return JsonResponse({"status": job.status, "done": job.done, "total": job.total, "errors": job.errors, "stalled": image_import.is_stalled(job)})

    # ---- bulk photo upload ------------------------------------------------------------
    def bulk_photos_view(self, request, object_id):
        product = self.get_object(request, str(object_id))
        if product is None or not self.has_change_permission(request, product):
            messages.error(request, "You don't have permission to edit this product's photos.")
            return redirect("admin:catalog_product_changelist")
        form = BulkPhotoForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            files = form.cleaned_data["photos"]
            problems = []
            max_files = SiteSettings.load_fresh().bulk_upload_max_files
            if len(files) > max_files:
                problems.append(f"Upload at most {max_files} photos at a time (change this in Site settings → Photo limits).")
            if sum(f.size for f in files) > MAX_BULK_BYTES:
                problems.append(f"These photos add up to more than {MAX_BULK_BYTES // (1024 * 1024)} MB. Upload fewer at a time.")
            for f in files:
                try:
                    validate_photo(f)
                except ValidationError as exc:
                    problems.append(f"{f.name}: {exc.messages[0]}")
            if problems:
                for msg in problems:
                    form.add_error("photos", msg)
            else:
                start = (product.images.order_by("-position").values_list("position", flat=True).first() or 0) + 1 if product.images.exists() else 0
                color, alt = form.cleaned_data["color"], form.cleaned_data["alt_text"]
                for offset, f in enumerate(files):
                    ProductImage.objects.create(product=product, image=f, color=color, alt_text=alt, position=start + offset)
                messages.success(request, f"Added {len(files)} photo(s) to “{product.name}”" + (f" for {color.name}." if color else " (shared by all colors)."))
                return redirect("admin:catalog_product_change", product.pk)
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "title": f"Upload photos — {product.name}",
            "product": product, "form": form, "existing": product.gallery_images, "max_files": SiteSettings.load_fresh().bulk_upload_max_files,
        }
        return render(request, "admin/catalog/product/bulk_photos.html", context)
