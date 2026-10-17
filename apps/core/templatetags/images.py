from django import template
from django.utils.html import format_html

from apps.core import images

register = template.Library()


@register.filter
def rendition(file, width):
    """`{{ photo|rendition:640 }}` -> URL of a resized WebP."""
    return images.rendition_url(file, int(width))


@register.simple_tag
def responsive_img(file, alt="", sizes="(max-width: 700px) 50vw, 320px", css="media-img", eager=False):
    """<img> with a WebP srcset so phones download a small file instead of the original upload."""
    if not file:
        return ""
    return format_html(
        '<img class="{}" src="{}" srcset="{}" sizes="{}" alt="{}" loading="{}" decoding="async">',
        css, images.rendition_url(file, 640), images.srcset(file), sizes, alt, "eager" if eager else "lazy",
    )
