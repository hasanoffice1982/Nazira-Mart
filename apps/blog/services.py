"""Blog helpers: safe article HTML, inline image uploads, reading time."""
from __future__ import annotations

import re
import uuid
from io import BytesIO

import nh3
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

from apps.core import images
from apps.core.richtext import ALLOWED_SCHEMES

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "hr", "a",
    "img", "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "width", "height"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 60_000_000
FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"""\bsrc\s*=\s*["']([^"']*)["']""", re.I)


def is_own_image(src: str) -> bool:
    """Only images stored on this site may appear in an article (other sites go away, and the page's security policy blocks them anyway)."""
    src = (src or "").strip()
    if src.startswith("//"):
        return False
    if src.startswith("/"):
        return True
    return src.startswith(settings.SITE_URL.rstrip("/") + "/")


def foreign_images(html: str) -> list[str]:
    """Image links in the raw article that point to other websites (they are removed on save)."""
    out = []
    for tag in _IMG_TAG.findall(html or ""):
        m = _SRC.search(tag)
        if m and not is_own_image(m.group(1)):
            out.append(m.group(1))
    return out


def _keep_own_image_src(tag: str, attr: str, value: str):
    if tag == "img" and attr == "src" and not is_own_image(value):
        return None
    return value


def clean_article_html(html: str) -> str:
    cleaned = nh3.clean(
        html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes=ALLOWED_SCHEMES, link_rel="noopener noreferrer",
        attribute_filter=_keep_own_image_src, set_tag_attribute_values={"img": {"loading": "lazy"}},
    )
    return re.sub(r"<img\b(?![^>]*\bsrc=)[^>]*>", "", cleaned)   # an image whose source was dropped would be a broken box


def plain_text(html: str) -> str:
    return re.sub(r"\s+", " ", nh3.clean(html or "", tags=set())).strip()


def reading_minutes(html: str) -> int:
    return max(1, round(len(plain_text(html).split()) / 200))


def store_inline_image(uploaded) -> str:
    """Save an image used inside an article as a 1600px WebP on this site and return its URL. Raises ValueError with a readable reason."""
    if uploaded is None:
        raise ValueError("No file was sent.")
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValueError("The image is bigger than 8 MB. Resize it and try again.")
    try:
        with Image.open(uploaded) as img:
            if img.format not in FORMATS:
                raise ValueError("Use a JPEG, PNG, WebP or GIF image.")
            if img.width * img.height > MAX_PIXELS:
                raise ValueError("The image is too large (more than 60 megapixels).")
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "P") else "RGB")
            if max(img.size) > images.ORIGINAL_MAX_SIDE:
                img.thumbnail((images.ORIGINAL_MAX_SIDE, images.ORIGINAL_MAX_SIDE), Image.LANCZOS)
            buf = BytesIO()
            img.save(buf, "WEBP", quality=images.ORIGINAL_QUALITY, method=4)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError("That file could not be read as an image.") from exc
    name = default_storage.save(f"blog/inline/{uuid.uuid4().hex}.webp", ContentFile(buf.getvalue()))
    return default_storage.url(name)
