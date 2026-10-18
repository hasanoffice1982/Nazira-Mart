from django import template
from django.utils.safestring import mark_safe

from apps.catalog.models import Category
from apps.core import seo_meta
from apps.core.models import SiteSettings

register = template.Library()


@register.simple_tag(takes_context=True)
def seo_tags(context, title="", description="", image="", og_type="website", product=None, crumbs=None, noindex=False, page_type="WebPage", category=None):
    """All search/social meta for the page. Call it in `{% block seo %}`; with no arguments it gives the site-wide defaults."""
    return mark_safe(seo_meta.build(
        context["request"], SiteSettings.load(), title=title, description=description, image=image, og_type=og_type, product=product,
        crumbs=crumbs, noindex=noindex, page_type=page_type, category=category,
    ))


@register.simple_tag(takes_context=True)
def product_seo_image(context, product):
    return seo_meta.image_url(product.cover_image) if product.cover_image else ""


@register.simple_tag
def category_for(slug):
    """The active Category object for a ?category=slug filter (None if there is none or it is unknown)."""
    return Category.objects.filter(slug=slug, is_active=True).first() if slug else None
