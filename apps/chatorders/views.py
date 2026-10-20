import hmac
import json
from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django_ratelimit.decorators import ratelimit

from apps.catalog.services import search_products

from . import services


def token_required(view):
    """The bot sends `Authorization: Bearer <CHAT_ORDER_TOKEN>`. With no token configured the API does not exist."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        expected = settings.CHAT_ORDER_TOKEN
        if not expected:
            raise Http404
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), expected.encode()):
            return JsonResponse({"error": "unauthorized"}, status=401)
        return view(request, *args, **kwargs)

    return wrapper


def _abs(request, path: str) -> str:
    return f"{settings.SITE_URL.rstrip('/')}{path}" if path.startswith("/") else path


@csrf_exempt
@require_GET
@token_required
@ratelimit(key="ip", rate="120/m", block=True)
def products(request):
    q = request.GET.get("q", "").strip()
    raw_limit = request.GET.get("limit", "5")
    limit = min(max(int(raw_limit), 1), 8) if raw_limit.isdigit() else 5
    out = []
    found = search_products(query=q)[:limit] if len(q) >= 2 else []
    for p in found:
        variants = [{"sku": v.sku, "label": v.label, "price": str(v.unit_price), "in_stock": v.stock > 0} for v in p.active_variants]
        cover = p.cover_image
        out.append({
            "sku": p.sku, "name": p.name, "category": p.category.name, "price": str(p.from_price), "price_varies": p.price_varies, "in_stock": p.in_stock,
            "url": _abs(request, p.get_absolute_url()), "image": _abs(request, cover.url) if cover else "", "variants": variants,
            "needs_variant": bool(variants),
        })
    return JsonResponse({"products": out})


@csrf_exempt
@require_POST
@token_required
@ratelimit(key="ip", rate="60/m", block=True)
def create_order(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "bad json"}, status=400)
    dry_run = bool(data.get("dry_run"))
    try:
        result, created = services.place(data, dry_run=dry_run)
    except services.ChatOrderError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    result.update(created=created, dry_run=dry_run, track_url=f"{settings.SITE_URL.rstrip('/')}/orders/track/")
    return JsonResponse(result, status=201 if created else 200)


@csrf_exempt
@require_GET
@token_required
@ratelimit(key="ip", rate="120/m", block=True)
def order_status(request):
    found = services.status_for(request.GET.get("phone", ""), request.GET.get("number", ""))
    if found is None:
        return JsonResponse({"error": "no order with that number and phone"}, status=404)
    return JsonResponse(found)
