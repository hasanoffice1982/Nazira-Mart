from django import template
from django.utils.html import strip_tags

from apps.core.richtext import rich as _rich

register = template.Library()
register.filter("rich", _rich)


@register.filter
def plain(text: str) -> str:
    """Tags removed, for meta descriptions."""
    return " ".join(strip_tags(text or "").split())
