"""Responsive WebP renditions generated from uploaded photos.

Originals are never sent to shoppers: every page uses resized WebP files (created once, then cached in
storage), so a 5 MB upload costs a few KB per card. Works with any Django storage backend.
"""
from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

logger = logging.getLogger("apps.core")

WIDTHS = (160, 320, 640, 960, 1280)
QUALITY = 80
ROOT = "renditions"
ORIGINAL_MAX_SIDE = 1600   # the stored original is only a source for renditions; 1280px is the largest one served
ORIGINAL_QUALITY = 85


def _name(source_name: str, width: int) -> str:
    digest = hashlib.sha1(source_name.encode()).hexdigest()[:20]
    return f"{ROOT}/{width}/{digest}.webp"


def _nearest(width: int) -> int:
    return min(WIDTHS, key=lambda w: (abs(w - width), w))


def rendition_name(source_name: str, width: int) -> str:
    return _name(source_name, _nearest(width))


def ensure_rendition(file, width: int) -> str | None:
    """Create the rendition if needed and return its storage name (None if the source can't be read)."""
    if not file:
        return None
    width = _nearest(width)
    name = _name(file.name, width)
    storage = default_storage
    if storage.exists(name):
        return name
    try:
        with storage.open(file.name, "rb") as fh, Image.open(fh) as img:
            img = ImageOps.exif_transpose(img)  # honour phone camera rotation, drop EXIF/GPS
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "P") else "RGB")
            if img.width > width:
                img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, "WEBP", quality=QUALITY, method=4)
    except Exception:  # noqa: BLE001
        logger.exception("could not build %spx rendition for %s", width, file.name)
        return None
    storage.save(name, ContentFile(buf.getvalue()))
    return name


def rendition_url(file, width: int) -> str:
    """URL of the resized WebP, falling back to the original if it can't be generated."""
    name = ensure_rendition(file, width)
    return default_storage.url(name) if name else (file.url if file else "")


def jpeg_rendition_url(file, width: int = 800) -> str:
    """URL of a resized JPEG copy. Messenger/WhatsApp image messages do not reliably accept WebP, so chat bots use this one.
    Created on first use and cached in storage; empty string if the source can't be read."""
    if not file:
        return ""
    digest = hashlib.sha1(file.name.encode()).hexdigest()[:20]
    name = f"{ROOT}/jpg{width}/{digest}.jpg"
    storage = default_storage
    if not storage.exists(name):
        try:
            with storage.open(file.name, "rb") as fh, Image.open(fh) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGBA")
                    flat = Image.new("RGB", img.size, (255, 255, 255))   # transparent areas become white
                    flat.paste(img, mask=img.getchannel("A"))
                    img = flat
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                if img.width > width:
                    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
                buf = BytesIO()
                img.save(buf, "JPEG", quality=85, optimize=True)
        except Exception:  # noqa: BLE001
            logger.exception("could not build jpeg copy of %s", file.name)
            return ""
        storage.save(name, ContentFile(buf.getvalue()))
    return storage.url(name)


def srcset(file, widths=(320, 640, 960)) -> str:
    parts = []
    for w in widths:
        name = ensure_rendition(file, w)
        if name:
            parts.append(f"{default_storage.url(name)} {_nearest(w)}w")
    return ", ".join(parts)


def delete_renditions(source_name: str) -> None:
    for w in WIDTHS:
        try:
            default_storage.delete(_name(source_name, w))
        except Exception:  # noqa: BLE001
            logger.warning("could not delete rendition %s", _name(source_name, w))


def pregenerate(file, widths=(320, 640, 960)) -> None:
    for w in widths:
        ensure_rendition(file, w)


def shrink_upload(field_file) -> None:
    """Replace a freshly uploaded photo with a 1600px WebP so 1000 products do not eat the disk (a 5 MB phone photo -> ~150 KB)."""
    if not field_file or getattr(field_file, "_committed", True):
        return
    try:
        field_file.seek(0)
        with Image.open(field_file) as img:
            img.draft("RGB", (ORIGINAL_MAX_SIDE * 2, ORIGINAL_MAX_SIDE * 2))  # JPEG: decode at reduced size, so a 100-megapixel photo needs little memory
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "P") else "RGB")
            if max(img.size) > ORIGINAL_MAX_SIDE:
                img.thumbnail((ORIGINAL_MAX_SIDE, ORIGINAL_MAX_SIDE), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, "WEBP", quality=ORIGINAL_QUALITY, method=4)
    except Exception:  # noqa: BLE001
        logger.exception("could not shrink upload %s; keeping the original", getattr(field_file, "name", ""))
        field_file.seek(0)
        return
    base = field_file.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    field_file.save(f"{base}.webp", ContentFile(buf.getvalue()), save=False)
