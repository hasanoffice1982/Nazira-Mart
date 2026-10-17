"""Rich text from the admin editor: sanitised to a small allow-list so staff input can never inject scripts."""
import re

import nh3
from django.utils.safestring import mark_safe

from .text import render_page_body

ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "s", "h2", "h3", "h4", "ul", "ol", "li", "blockquote", "hr", "a"}
ALLOWED_ATTRS = {"a": {"href", "title"}}
ALLOWED_SCHEMES = {"http", "https", "mailto", "tel"}
_HAS_TAGS = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def looks_like_html(text: str) -> bool:
    return bool(_HAS_TAGS.search(text or ""))


def clean_html(html: str) -> str:
    return nh3.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes=ALLOWED_SCHEMES, link_rel="noopener noreferrer nofollow")


def rich(text: str):
    """Safe HTML for display. Older plain-text content (blank-line paragraphs, '## ', '- ') still renders correctly."""
    text = text or ""
    return mark_safe(clean_html(text) if looks_like_html(text) else render_page_body(text))


def sanitize_field(text: str) -> str:
    return clean_html(text) if looks_like_html(text) else text
