import hmac
import json

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services


@csrf_exempt
@require_POST
def webhook(request, token: str):
    """Supplier status updates. The secret is part of the URL, so only the supplier (who we gave the URL to) can call it."""
    expected = settings.SUPPLIER_WEBHOOK_TOKEN
    if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
        raise Http404
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return HttpResponse("bad json", status=400)
    if not isinstance(payload, dict):
        return HttpResponse("bad payload", status=400)
    return JsonResponse({"ok": services.apply_webhook(payload)})
