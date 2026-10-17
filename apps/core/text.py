"""Render admin-written page text to HTML. Everything is escaped first, so staff input can never inject markup."""
import re

from django.utils.html import escape, urlize
from django.utils.safestring import mark_safe

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    html = escape(text)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return urlize(html, nofollow=True, autoescape=False)


def render_page_body(text: str):
    blocks, para, items = [], [], []

    def flush_para():
        if para:
            blocks.append(f"<p>{'<br>'.join(_inline(l) for l in para)}</p>")
            para.clear()

    def flush_items():
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush_para()
            flush_items()
        elif line.startswith("## "):
            flush_para()
            flush_items()
            blocks.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("- "):
            flush_para()
            items.append(line[2:])
        else:
            flush_items()
            para.append(line)
    flush_para()
    flush_items()
    return mark_safe("\n".join(blocks))
