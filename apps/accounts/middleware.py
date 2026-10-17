from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import twofactor

ALLOWED_PREFIXES = ("/accounts/2fa/", "/accounts/logout/", "/static/", "/media/", "/sw.js", "/manifest.webmanifest", "/offline/")


class TwoFactorMiddleware:
    """Default-deny gate: a password-authenticated session that still owes the emailed code can only reach the
    challenge page (and logout/static). This covers the storefront and the admin alike, including stale sessions."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if twofactor.needs_challenge(request) and not request.path.startswith(ALLOWED_PREFIXES):
            url = reverse("accounts:two_factor")
            if request.headers.get("HX-Request"):
                resp = HttpResponse(status=204)
                resp["HX-Redirect"] = url
                return resp
            if request.method == "GET":
                twofactor.remember_next(request, request.get_full_path())
            return redirect(url)
        return self.get_response(request)
