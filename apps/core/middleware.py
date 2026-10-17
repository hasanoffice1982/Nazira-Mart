from csp.constants import UNSAFE_INLINE
from django.conf import settings

from . import tracking
from .models import SiteSettings


class AdminCSPRelaxMiddleware:
    """Django admin / Jazzmin ship inline scripts, so the admin path gets a looser script policy.

    Must sit *after* CSPMiddleware in MIDDLEWARE (so its process_response runs first).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/" + settings.ADMIN_URL):
            response._csp_update = {"script-src": [UNSAFE_INLINE]}
        else:
            extra = tracking.csp_update(SiteSettings.load())
            if extra:
                response._csp_update = extra
        return response
