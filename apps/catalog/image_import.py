"""Photos for CSV imports.

A CSV cell lists photos separated by `|`. Each entry is either a filename (found inside the ZIP uploaded with the CSV)
or a public http(s) link. Products and variants are imported first (fast, all-or-nothing); the photos are then processed
by a background job with a progress page, because thousands of photos cannot finish inside one web request.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.utils import timezone

from .models import MAX_IMAGE_BYTES, Color, ImageImportJob, PendingPhoto, Product, ProductImage, validate_photo

logger = logging.getLogger("apps.catalog")

MAX_ZIPS = 6                              # parts in one import
MAX_TOTAL_ZIP_BYTES = 1024 * 1024 * 1024  # all parts together
MAX_ZIP_BYTES = 500 * 1024 * 1024        # sent in CHUNK_SIZE pieces, because Cloudflare rejects any single request above 100 MB
CHUNK_SIZE = 20 * 1024 * 1024
MAX_CHUNKS = 40
MAX_ZIP_ENTRIES = 3000
MAX_UNPACKED_BYTES = 1500 * 1024 * 1024    # sum of declared sizes: refuses zip bombs before reading anything
MAX_REFS = 1500                            # photos per import
MAX_URLS = 150                             # links per import (use the ZIP for big batches)
MAX_URL_IMAGE_BYTES = 15 * 1024 * 1024   # photos fetched from links are held to a smaller limit than uploaded ones
URL_TIMEOUT = 10
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
STALL_SECONDS = 120


class ImportImageError(Exception):
    pass


def max_per_product() -> int:
    """Admin -> Site settings -> Photo limits (read fresh, so a change applies to the next import)."""
    from apps.core.models import SiteSettings

    return SiteSettings.load_fresh().max_photos_per_product


def is_url(ref: str) -> bool:
    return ref.lower().startswith(("http://", "https://"))


_LOOKS_LIKE_FILE = re.compile(r"\.(jpe?g|png|webp)$", re.IGNORECASE)


def split_refs(cell: str) -> list[str]:
    """Photos in one cell: separated by | ; a new line or a comma. A comma inside a link's query string stays part of the link."""
    entries: list[str] = []
    for block in re.split(r"[|;\n\r]+", cell or ""):
        current = ""
        for token in block.split(","):
            piece = token.strip()
            if not current:
                current = piece
            elif is_url(current) and not is_url(piece) and not _LOOKS_LIKE_FILE.search(piece):
                current += "," + token      # a comma that belongs to the link
            else:
                entries.append(current)
                current = piece
        entries.append(current)
    seen, out = set(), []
    for ref in (e.strip() for e in entries):
        if ref and ref.lower() not in seen:
            seen.add(ref.lower())
            out.append(ref)
    return out


def tmp_dir() -> Path:
    path = Path(settings.IMPORT_TMP_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def purge_old_uploads(max_age_hours: int = 24) -> None:
    cutoff = time.time() - max_age_hours * 3600
    for f in [*tmp_dir().glob("*.zip"), *tmp_dir().glob("*.part")]:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def paths_of(value) -> list[str]:
    """A stored ZIP location: one path, several paths separated by newlines, or a list."""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split("\n")
    return [str(i).strip() for i in items if str(i).strip()]


def drop_paths(value) -> None:
    for path in paths_of(value):
        Path(path).unlink(missing_ok=True)


class ImageBank:
    """Read-only view of the uploaded ZIP file(s). Files are matched by name only and never written to disk by that name.

    Several ZIPs (for example photos split into parts) can be searched together as one collection."""

    def __init__(self, paths=None):
        self.paths = paths_of(paths)
        self.path = "\n".join(self.paths)  # truthy when any ZIP was given
        self.full: dict[str, tuple[str, str]] = {}   # "folder/name.jpg" -> (zip path, member)
        self.base: dict[str, tuple[str, str]] = {}   # "name.jpg" -> (zip path, member)
        self.ambiguous: set[str] = set()
        self.duplicates: set[str] = set()
        self.error = ""
        self._entries = 0
        self._unpacked = 0
        for path in self.paths:
            self._index(path)
            if self.error:
                break

    def _index(self, path: str) -> None:
        try:
            with zipfile.ZipFile(path) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                self._entries += len(infos)
                self._unpacked += sum(i.file_size for i in infos)
                if self._entries > MAX_ZIP_ENTRIES:
                    self.error = f"The ZIPs have more than {MAX_ZIP_ENTRIES} files in total"
                    return
                if self._unpacked > MAX_UNPACKED_BYTES:
                    self.error = "The ZIPs are too large once unpacked"
                    return
                for info in infos:
                    parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                    if not parts or "__MACOSX" in parts or parts[-1].startswith(".") or info.flag_bits & 1:
                        continue
                    name, where = parts[-1].lower(), (path, info.filename)
                    full = "/".join(parts).lower()
                    if full in self.full and self.full[full] != where:
                        self.duplicates.add(full)   # the very same path exists in two ZIPs
                    self.full[full] = where
                    if name in self.base and self.base[name] != where:
                        self.ambiguous.add(name)
                    self.base[name] = where
        except (zipfile.BadZipFile, OSError):
            self.error = "That file isn't a valid ZIP"

    def lookup(self, ref: str) -> tuple[str, str] | None:
        key = ref.replace("\\", "/").strip("/").lower()
        if key in self.duplicates:
            raise ImportImageError(f"“{ref}” exists in more than one ZIP; keep it in only one")
        if key in self.full:
            return self.full[key]
        name = key.rsplit("/", 1)[-1]
        if name in self.ambiguous:
            raise ImportImageError(f"“{ref}” matches several files in the ZIPs; include its folder, e.g. folder/{name}")
        return self.base.get(name)

    def read(self, ref: str) -> bytes:
        where = self.lookup(ref)
        if where is None:
            raise ImportImageError(f"“{ref}” was not found in the ZIP")
        path, member = where
        with zipfile.ZipFile(path) as zf, zf.open(member) as fh:
            data = fh.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ImportImageError(f"“{ref}” is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        return data


# ---- links ------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _check_public_host(host: str) -> None:
    """Refuse addresses that are not on the public internet (localhost, LAN, cloud metadata, ...)."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        raise ImportImageError(f"could not look up {host}")
    if not addresses or any(not ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise ImportImageError(f"{host} is not a public website")


def fetch_url(url: str) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(4):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.port not in (None, 80, 443):
            raise ImportImageError(f"“{url[:80]}” is not a normal http(s) link")
        _check_public_host(parts.hostname)
        req = urllib.request.Request(url, headers={"User-Agent": "NarizMartImporter/1.0", "Accept": "image/*"})
        try:
            with opener.open(req, timeout=URL_TIMEOUT) as resp:
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_URL_IMAGE_BYTES:
                    raise ImportImageError(f"“{url[:80]}” is larger than {MAX_URL_IMAGE_BYTES // (1024 * 1024)} MB")
                data = resp.read(MAX_URL_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
                url = urljoin(url, exc.headers["Location"])
                continue
            raise ImportImageError(f"“{url[:80]}” answered with error {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ImportImageError(f"“{url[:80]}” could not be downloaded")
        if len(data) > MAX_URL_IMAGE_BYTES:
            raise ImportImageError(f"“{url[:80]}” is larger than {MAX_URL_IMAGE_BYTES // (1024 * 1024)} MB")
        return data
    raise ImportImageError(f"“{url[:80]}” redirects too many times")


# ---- background job ---------------------------------------------------------------------------------

def _load(ref: str, bank: ImageBank) -> bytes:
    data = fetch_url(ref) if is_url(ref) else bank.read(ref)
    try:
        validate_photo(SimpleUploadedFile("photo", data))
    except ValidationError as exc:
        raise ImportImageError(f"“{ref[:80]}”: {exc.messages[0]}")
    return data


def _ext(ref: str) -> str:
    ext = Path(urlparse(ref).path).suffix.lower()
    return ext if ext in PHOTO_EXTS else ".jpg"


def _process_product(entry: dict, bank: ImageBank) -> None:
    """Attach photos. Normally the listed photos REPLACE the product's photos of that kind (shared, or one color), so repeating an
    import never duplicates. With "append" (photos uploaded later for names saved from a CSV) existing photos stay and the new ones
    are added after them."""
    append = bool(entry.get("append"))
    shared = [(r, _load(r, bank)) for r in entry.get("shared", [])]
    colors = {name: [(r, _load(r, bank)) for r in refs] for name, refs in entry.get("colors", {}).items()}
    with transaction.atomic():
        product = Product.objects.get(slug=entry["slug"])
        position = 0
        if append:
            last = product.images.order_by("-position").values_list("position", flat=True).first()
            position = 0 if last is None else last + 1
        if shared:
            if not append:
                product.images.filter(color__isnull=True).delete()
            for ref, data in shared:
                ProductImage.objects.create(product=product, image=ContentFile(data, name=f"{product.slug}-{position + 1}{_ext(ref)}"), alt_text=product.name[:160], position=position)
                position += 1
        for name, files in colors.items():
            color = Color.objects.filter(name__iexact=name).first()
            if color is None:
                raise ImportImageError(f"color “{name}” does not exist")
            if not append:
                product.images.filter(color=color).delete()
            for ref, data in files:
                ProductImage.objects.create(product=product, image=ContentFile(data, name=f"{product.slug}-{color.name.lower()}-{position + 1}{_ext(ref)}"), color=color, alt_text=f"{product.name} – {color.name}"[:160], position=position)
                position += 1
        if entry.get("pending_ids"):
            PendingPhoto.objects.filter(pk__in=entry["pending_ids"]).delete()  # these names now have their photos


def match_pending(bank: ImageBank) -> tuple[list[dict], int, list[str], list[str]]:
    """Find waiting photo names that have a file in the uploaded ZIP(s).

    Returns (job entries, number of photos matched, file names in the ZIPs that matched nothing, ambiguous names)."""
    entries: dict[str, dict] = {}
    matched_keys: set[str] = set()
    ambiguous: set[str] = set()
    count = 0
    for pending in PendingPhoto.objects.select_related("product", "color").order_by("product_id", "position", "id"):
        key = pending.key
        if key in bank.ambiguous or key in bank.duplicates:
            ambiguous.add(key)
            continue
        if key not in bank.base:
            continue
        entry = entries.setdefault(pending.product.slug, {"slug": pending.product.slug, "shared": [], "colors": {}, "append": True, "pending_ids": []})
        if pending.color_id:
            entry["colors"].setdefault(pending.color.name, []).append(key)
        else:
            entry["shared"].append(key)
        entry["pending_ids"].append(pending.pk)
        matched_keys.add(key)
        count += 1
    unmatched = sorted(name for name in bank.base if name not in matched_keys and name not in ambiguous and Path(name).suffix.lower() in PHOTO_EXTS)
    return list(entries.values()), count, unmatched, sorted(ambiguous)


def run_job(job_id) -> None:
    job = ImageImportJob.objects.get(pk=job_id)
    job.status = ImageImportJob.Status.RUNNING
    job.save(update_fields=["status", "updated_at"])
    bank = ImageBank(job.zip_path)
    errors: list[str] = []
    try:
        for entry in job.plan:
            try:
                _process_product(entry, bank)
            except ImportImageError as exc:
                errors.append(f"{entry['slug']}: {exc}")
            except Exception:  # noqa: BLE001
                logger.exception("photo import failed for %s", entry.get("slug"))
                errors.append(f"{entry.get('slug')}: unexpected error (see server log)")
            job.done += 1
            job.errors = errors[:200]
            job.save(update_fields=["done", "errors", "updated_at"])
        job.status = ImageImportJob.Status.DONE
    except Exception:  # noqa: BLE001
        logger.exception("photo import job %s crashed", job_id)
        job.status = ImageImportJob.Status.FAILED
    finally:
        job.save(update_fields=["status", "updated_at"])
        drop_paths(job.zip_path)


def _run_in_thread(job_id) -> None:
    try:
        run_job(job_id)
    finally:
        connection.close()  # this thread opened its own database connection


def start_job(job: ImageImportJob) -> None:
    if getattr(settings, "IMAGE_IMPORT_ASYNC", True):
        threading.Thread(target=_run_in_thread, args=(job.pk,), daemon=True, name=f"photo-import-{job.pk}").start()
    else:
        run_job(job.pk)


def is_stalled(job: ImageImportJob) -> bool:
    return job.status in (ImageImportJob.Status.QUEUED, ImageImportJob.Status.RUNNING) and (timezone.now() - job.updated_at).total_seconds() > STALL_SECONDS


# ---- chunked ZIP upload -----------------------------------------------------------------------------
UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


class ChunkError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def zip_path_for(token: str) -> Path | None:
    """The finished upload for a token, or None (also for anything that is not a plain 32-hex token)."""
    if not UPLOAD_ID.match(token or ""):
        return None
    path = tmp_dir() / f"{token}.zip"
    return path if path.is_file() else None


def receive_chunk(upload_id: str, index: int, total: int, chunk) -> str | None:
    """Append one piece of a ZIP. Pieces must arrive in order and be exactly CHUNK_SIZE except the last.
    Returns the token when the last piece has been stored and the result is a valid ZIP, otherwise None."""
    if not UPLOAD_ID.match(upload_id or ""):
        raise ChunkError("Bad upload id.")
    if not (1 <= total <= MAX_CHUNKS) or not (0 <= index < total):
        raise ChunkError("Bad chunk numbers.")
    last = index == total - 1
    if chunk.size > CHUNK_SIZE or (not last and chunk.size != CHUNK_SIZE) or chunk.size == 0:
        raise ChunkError("Unexpected chunk size.")
    part = tmp_dir() / f"{upload_id}.part"
    if index == 0:
        part.unlink(missing_ok=True)
    elif not part.exists() or part.stat().st_size != index * CHUNK_SIZE:
        raise ChunkError("A piece of the upload is missing. Choose the file again.", 409)
    if (index * CHUNK_SIZE) + chunk.size > MAX_ZIP_BYTES:
        part.unlink(missing_ok=True)
        raise ChunkError(f"The ZIP is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB.", 413)
    with open(part, "ab") as out:
        for piece in chunk.chunks():
            out.write(piece)
    if not last:
        return None
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise ChunkError("That file isn't a valid ZIP.")
    part.replace(tmp_dir() / f"{upload_id}.zip")
    return upload_id
