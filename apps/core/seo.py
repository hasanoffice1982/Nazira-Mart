"""sitemap.xml and robots.txt. Links always use SITE_URL, so every entry points at the one canonical domain."""
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import cache_page

from apps.catalog.models import Category, Product

from .models import Page


class _Canonical:
    def __init__(self):
        parts = urlparse(settings.SITE_URL)
        self.domain = parts.netloc
        self.scheme = parts.scheme or "https"


class StaticSitemap(Sitemap):
    changefreq, priority = "daily", 0.8

    def items(self):
        return [("catalog:home", 1.0), ("catalog:shop", 0.9), ("core:contact", 0.3)]

    def location(self, item):
        return reverse(item[0])

    def priority_for(self, item):
        return item[1]


class CategorySitemap(Sitemap):
    changefreq, priority = "daily", 0.7

    def items(self):
        return Category.objects.filter(is_active=True)

    def location(self, obj):
        return f"{reverse('catalog:shop')}?category={obj.slug}"


class ProductSitemap(Sitemap):
    changefreq, priority = "weekly", 0.8

    def items(self):
        return Product.objects.filter(is_active=True).order_by("id")


class PageSitemap(Sitemap):
    changefreq, priority = "monthly", 0.4

    def items(self):
        return Page.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at


SITEMAPS = [StaticSitemap(), CategorySitemap(), ProductSitemap(), PageSitemap()]


@cache_page(60 * 15)
def sitemap_xml(request):
    site = _Canonical()
    urls = []
    for sm in SITEMAPS:
        urls.extend(sm.get_urls(site=site, protocol=site.scheme))
    for u in urls:
        if isinstance(u.get("item"), tuple):  # the static pages carry their own priority
            u["priority"] = str(u["item"][1])
    return render(request, "sitemap.xml", {"urlset": urls}, content_type="application/xml")


@cache_page(60 * 60)
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /cart/",
        "Disallow: /orders/",
        "Disallow: /accounts/",
        "Disallow: /wishlist/",
        "Disallow: /shop/fragment/",
        "Disallow: /*?q=",
        "Disallow: /*&q=",
        "",
        f"Sitemap: {settings.SITE_URL.rstrip('/')}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
