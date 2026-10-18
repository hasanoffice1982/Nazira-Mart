"""Search and social meta for every page: description, canonical, robots, Open Graph, Twitter and JSON-LD.

Everything is built from the live data (Site settings, product, category, page), so it never goes stale.
Only facts we really know go into the structured data: no star ratings are published, because the stored rating is a placeholder.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.files.storage import default_storage
from django.utils.html import escape, strip_tags

from .images import ensure_rendition

DEFAULT_DESCRIPTION = "NarizMart: fashion, beauty, jewellery, bags, toys and kids’ favourites. Pretty finds for ladies and little ones, delivered across Bangladesh with cash on delivery."
NOINDEX_PREFIXES = ("/cart/", "/orders/", "/accounts/", "/wishlist/", "/supplier/", "/shop/fragment/")
CANONICAL_PARAMS = ("category", "collection")   # filters that are real pages; sort, search and tracking parameters are dropped
RETURN_DAYS = 7
TRANSIT_DAYS = (1, 5)


def base_url() -> str:
    return settings.SITE_URL.rstrip("/")


def absolute(url: str) -> str:
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else base_url() + (url if url.startswith("/") else "/" + url)


def clean(text: str, limit: int = 160) -> str:
    text = " ".join(strip_tags(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"


def canonical_url(request) -> str:
    params = {k: request.GET[k] for k in CANONICAL_PARAMS if request.GET.get(k)}
    page = request.GET.get("page", "")
    if page.isdigit() and int(page) > 1:
        params["page"] = page
    return base_url() + request.path + (("?" + urlencode(params)) if params else "")


def is_noindex(request, forced: bool = False) -> bool:
    if forced or request.path.startswith(NOINDEX_PREFIXES) or request.path.startswith("/" + settings.ADMIN_URL):
        return True
    return bool(request.GET.get("q"))


def image_url(file, width: int = 1200) -> str:
    """Absolute URL of a resized WebP rendition of an uploaded photo, or '' if there is none."""
    if not file:
        return ""
    name = ensure_rendition(file, width)
    return absolute(default_storage.url(name)) if name else ""


def default_image() -> str:
    return absolute(staticfiles_storage.url("img/og-default.png"))


def logo_url() -> str:
    return absolute(staticfiles_storage.url("img/logo.png"))


# --- JSON-LD nodes -------------------------------------------------------------------------------
def organization(site) -> dict:
    node = {
        "@type": "Organization", "@id": base_url() + "/#organization", "name": site.company_name, "url": base_url() + "/",
        "logo": {"@type": "ImageObject", "url": logo_url()}, "image": default_image(), "description": DEFAULT_DESCRIPTION,
    }
    email = site.effective_contact_email
    if email:
        node["email"] = email
    if site.contact_phone:
        node["telephone"] = site.contact_phone
    node["address"] = {"@type": "PostalAddress", "addressCountry": "BD", **({"addressLocality": site.contact_address.replace("🇧🇩", "").strip()} if site.contact_address else {})}
    links = [url for _label, _short, url in site.social_links]
    if links:
        node["sameAs"] = links
    contact = {"@type": "ContactPoint", "contactType": "customer service", "availableLanguage": ["en", "bn"], "areaServed": "BD"}
    if site.contact_phone:
        contact["telephone"] = site.contact_phone
    if email:
        contact["email"] = email
    node["contactPoint"] = contact
    return node


def website(site) -> dict:
    return {
        "@type": "WebSite", "@id": base_url() + "/#website", "url": base_url() + "/", "name": site.company_name,
        "publisher": {"@id": base_url() + "/#organization"}, "inLanguage": "en",
        "potentialAction": {"@type": "SearchAction", "target": {"@type": "EntryPoint", "urlTemplate": base_url() + "/shop/?q={search_term_string}"}, "query-input": "required name=search_term_string"},
    }


def breadcrumbs(crumbs: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i, "name": name, "item": absolute(url)} for i, (name, url) in enumerate(crumbs, 1)],
    }


def product_node(product, site, url: str) -> dict:
    images = [u for u in (image_url(i.image) for i in product.gallery_images[:6]) if u]
    if not images and product.image:
        images = [u for u in [image_url(product.image)] if u]
    in_stock = product.in_stock
    availability = "https://schema.org/InStock" if in_stock else "https://schema.org/OutOfStock"
    seller = {"@id": base_url() + "/#organization"}
    common = {
        "priceCurrency": site.currency_code, "availability": availability, "itemCondition": "https://schema.org/NewCondition", "url": url, "seller": seller,
        "shippingDetails": {
            "@type": "OfferShippingDetails",
            "shippingRate": {"@type": "MonetaryAmount", "value": str(site.delivery_charge_dhaka), "currency": site.currency_code},
            "shippingDestination": {"@type": "DefinedRegion", "addressCountry": "BD"},
            "deliveryTime": {
                "@type": "ShippingDeliveryTime",
                "handlingTime": {"@type": "QuantitativeValue", "minValue": 0, "maxValue": 1, "unitCode": "DAY"},
                "transitTime": {"@type": "QuantitativeValue", "minValue": TRANSIT_DAYS[0], "maxValue": TRANSIT_DAYS[1], "unitCode": "DAY"},
            },
        },
        "hasMerchantReturnPolicy": {
            "@type": "MerchantReturnPolicy", "applicableCountry": "BD", "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
            "merchantReturnDays": RETURN_DAYS, "returnMethod": "https://schema.org/ReturnByMail",
        },
    }
    prices = sorted({v.unit_price for v in product.active_variants}) if product.has_variants else [product.price]
    if len(prices) > 1:
        offers = {"@type": "AggregateOffer", "lowPrice": str(prices[0]), "highPrice": str(prices[-1]), "offerCount": len(product.active_variants), **common}
    else:
        offers = {"@type": "Offer", "price": str(prices[0]), **common}
    node = {
        "@type": "Product", "@id": url + "#product", "name": product.name, "url": url, "sku": product.sku, "mpn": product.sku,
        "description": clean(product.description, 5000) or clean(product.name), "category": product.category.name,
        "brand": {"@type": "Brand", "name": site.company_name}, "offers": offers,
    }
    if images:
        node["image"] = images
    return node


def auto_crumbs(request, title: str, product, category) -> list[tuple[str, str]]:
    """Home > Shop > Category > Product, or Home > Page, worked out from what the page shows."""
    if request.path == "/":
        return []
    trail = [("Home", "/")]
    cat = category or (product.category if product is not None else None)
    if product is not None or cat is not None:
        trail.append(("Shop", "/shop/"))
        if cat is not None:
            trail.append((cat.name, f"/shop/?category={cat.slug}"))
        if product is not None:
            trail.append((product.name, product.get_absolute_url()))
    elif title:
        trail.append((title, request.path))
    return trail


def dumps(data: dict) -> str:
    """JSON that is safe inside a <script> block (no way to close the tag or break the page)."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build(request, site, *, title: str = "", description: str = "", image: str = "", og_type: str = "website", product=None,
          crumbs: list[tuple[str, str]] | None = None, noindex: bool = False, page_type: str = "WebPage", extra: list[dict] | None = None, category=None) -> str:
    url = canonical_url(request)
    if crumbs is None:
        crumbs = auto_crumbs(request, title, product, category)
    full_title = f"{title} — {site.company_name}" if title else f"{site.company_name} — Pretty finds for ladies & kids"
    desc = clean(description) or DEFAULT_DESCRIPTION
    img = image or default_image()
    robots = "noindex,follow" if is_noindex(request, noindex) else "index,follow,max-image-preview:large"
    tags = [
        f'<meta name="description" content="{escape(desc)}">',
        f'<meta name="robots" content="{robots}">',
        f'<link rel="canonical" href="{escape(url)}">',
        f'<meta property="og:site_name" content="{escape(site.company_name)}">',
        '<meta property="og:locale" content="en_US">',
        f'<meta property="og:type" content="{escape(og_type)}">',
        f'<meta property="og:title" content="{escape(full_title)}">',
        f'<meta property="og:description" content="{escape(desc)}">',
        f'<meta property="og:url" content="{escape(url)}">',
        f'<meta property="og:image" content="{escape(img)}">',
        f'<meta property="og:image:alt" content="{escape(title or site.company_name)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{escape(full_title)}">',
        f'<meta name="twitter:description" content="{escape(desc)}">',
        f'<meta name="twitter:image" content="{escape(img)}">',
    ]
    if product is not None:
        tags += [
            f'<meta property="product:price:amount" content="{product.from_price}">',
            f'<meta property="product:price:currency" content="{escape(site.currency_code)}">',
            f'<meta property="product:availability" content="{"in stock" if product.in_stock else "out of stock"}">',
            f'<meta property="product:retailer_item_id" content="{escape(product.sku)}">',
            '<meta property="product:condition" content="new">',
        ]
    graph = [organization(site), website(site), {"@type": page_type, "@id": url + "#webpage", "url": url, "name": full_title, "description": desc,
                                                 "isPartOf": {"@id": base_url() + "/#website"}, "inLanguage": "en", "primaryImageOfPage": {"@type": "ImageObject", "url": img}}]
    if crumbs:
        graph.append(breadcrumbs(crumbs))
    if product is not None:
        graph.append(product_node(product, site, url))
    graph += extra or []
    tags.append('<script type="application/ld+json">' + dumps({"@context": "https://schema.org", "@graph": graph}) + "</script>")
    return "\n".join(tags)
