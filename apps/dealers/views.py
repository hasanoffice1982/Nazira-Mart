import json

from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import Dealer


@csrf_exempt
@require_POST
def webhook(request, token: str):
    """Tracking and status from a dealer's automation. The long secret in the address identifies (and authenticates) the dealer."""
    dealer = Dealer.objects.filter(token=token, is_active=True).first()
    if dealer is None:
        raise Http404
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return HttpResponse("bad json", status=400)
    if not isinstance(payload, dict):
        return HttpResponse("bad payload", status=400)
    return JsonResponse({"ok": services.apply_update(dealer, payload)})
