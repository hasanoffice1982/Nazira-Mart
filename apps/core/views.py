import logging
from pathlib import Path

from django.conf import settings
from django import forms
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import defaults
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited
from django.templatetags.static import static
from django.core.mail import EmailMessage
from django.http import Http404
from django.views.generic import FormView, TemplateView, View

from .forms import HONEYPOT_NAME, ContactForm
from .htmx import hx_trigger
from .models import ContactMessage, NewsletterSubscriber, Page, SiteSettings
from .richtext import rich


class OfflineView(TemplateView):
    template_name = "offline.html"


class ManifestView(View):
    def get(self, request):
        data = {
            "name": "NarizMart",
            "short_name": "NarizMart",
            "description": "Your everyday, made better.",
            "id": "/",
            "start_url": "/?source=pwa",
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#ffffff",
            "theme_color": "#0a3550",
            "icons": [
                {"src": static("pwa/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                {"src": static("pwa/icon-512.png"), "sizes": "512x512", "type": "image/png"},
                {"src": static("pwa/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
            "shortcuts": [
                {"name": "My orders", "url": "/orders/"},
                {"name": "Wishlist", "url": "/wishlist/"},
            ],
        }
        return JsonResponse(data, content_type="application/manifest+json")


PRECACHE_STATIC = [
    "css/fonts.css", "css/narizmart.css", "css/app.css", "vendor/swiper/swiper-bundle.min.css",
    "js/htmx.min.js", "vendor/swiper/swiper-bundle.min.js", "js/narizmart-art.js", "js/app.js",
    "fonts/dm-sans-latin-wght-normal.woff2", "img/logo.png", "pwa/icon-192.png",
]


def sw_context() -> dict:
    """Precache list + release version. Production file names are content-hashed, so any change yields a new
    version (and a new sw.js), which is what tells browsers to update."""
    import hashlib
    import json

    from django.contrib.staticfiles import finders
    from django.contrib.staticfiles.storage import staticfiles_storage

    urls = [staticfiles_storage.url(p) for p in PRECACHE_STATIC]
    digest = hashlib.sha1(("|".join(urls) + settings.APP_VERSION).encode())
    if settings.DEBUG:  # dev file names aren't hashed, so fold file mtimes in to see edits
        for p in PRECACHE_STATIC:
            found = finders.find(p)
            if found:
                digest.update(str(Path(found).stat().st_mtime_ns).encode())
    return {
        "version": digest.hexdigest()[:12], "precache_json": json.dumps(urls), "offline_url": reverse("core:offline"),
        "content_hashed": "false" if settings.DEBUG else "true",
    }


class ServiceWorkerView(View):
    """Served from the site root so the worker can control the whole origin."""

    def get(self, request):
        resp = render(request, "pwa/sw.js", sw_context(), content_type="application/javascript")
        resp["Cache-Control"] = "no-cache"
        resp["Service-Worker-Allowed"] = "/"
        return resp


@method_decorator(ratelimit(key="ip", rate="5/h", method="POST", block=True), name="post")
class NewsletterSubscribeView(View):
    def post(self, request):
        if request.POST.get(HONEYPOT_NAME):  # bot: pretend it worked, store nothing
            return render(request, "components/newsletter_form.html", {"done": True, "created": True})
        field = forms.EmailField()
        try:
            email = field.clean(request.POST.get("email", "").strip().lower())
        except forms.ValidationError:
            return render(request, "components/newsletter_form.html", {"error": "Please enter a valid email address."})
        _, created = NewsletterSubscriber.objects.get_or_create(email=email)
        return render(request, "components/newsletter_form.html", {"done": True, "created": created})


def forbidden(request, exception=None):
    """403 handler: friendly 429 for rate-limited requests, Django's default otherwise."""
    if isinstance(exception, Ratelimited):
        if request.headers.get("HX-Request"):
            resp = HttpResponse(status=429)
            return hx_trigger(resp, toast="Too many requests — please slow down and try again shortly.")
        resp = render(request, "429.html", status=429)
        resp["Retry-After"] = "60"
        return resp
    return defaults.permission_denied(request, exception)


class PageView(TemplateView):
    template_name = "core/page.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        page = Page.objects.filter(slug=kwargs["slug"], is_published=True).first()
        if page is None:
            raise Http404
        ctx.update(page=page, body_html=rich(SiteSettings.load_fresh().render_tokens(page.body)))
        return ctx


@method_decorator(ratelimit(key="ip", rate="5/h", method="POST", block=True), name="post")
@method_decorator(ratelimit(key="post:email", rate="3/h", method="POST", block=True), name="post")
class ContactView(FormView):
    template_name = "core/contact.html"
    form_class = ContactForm

    def get_initial(self):
        user = self.request.user
        initial = {"order_number": self.request.GET.get("order", "")}
        if user.is_authenticated:
            initial.update(name=user.get_full_name() or user.first_name, email=user.email, phone=user.phone)
        return initial

    def form_valid(self, form):
        data = form.cleaned_data
        msg = ContactMessage.objects.create(**{k: data[k] for k in ("name", "email", "phone", "order_number", "message")})
        body = (
            f"From: {msg.name} <{msg.email}>\nPhone: {msg.phone or '-'}\nOrder: {msg.order_number or '-'}\n\n{msg.message}\n"
        )
        try:
            EmailMessage(
                subject=f"[Contact] {msg.name}" + (f" — {msg.order_number}" if msg.order_number else ""),
                body=body, to=[SiteSettings.load_fresh().effective_contact_email], reply_to=[msg.email],
            ).send()
        except Exception:  # noqa: BLE001  (the message is saved, so nothing is lost)
            logging.getLogger("apps.core").exception("contact email failed id=%s", msg.pk)
        self.request.session["contact_sent"] = {"name": msg.name, "email": msg.email}
        return redirect("core:contact_sent")  # redirect after POST: refresh or Back can't send it twice, and /contact/ is an empty form again


class ContactSentView(TemplateView):
    template_name = "core/contact_done.html"

    def get(self, request, *args, **kwargs):
        sent = request.session.get("contact_sent")
        if not sent:
            return redirect("core:contact")
        return render(request, self.template_name, {"contact": sent})


def healthz(request):
    """Liveness + database check for the deploy script, load balancers and uptime monitors. No private data."""
    from django.db import connection
    from django.views.decorators.cache import never_cache

    @never_cache
    def check(_request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:  # noqa: BLE001
            logging.getLogger("apps.core").exception("health check: database unavailable")
            return JsonResponse({"status": "error"}, status=503)
        return JsonResponse({"status": "ok", "release": settings.APP_RELEASE, "version": settings.APP_VERSION})

    return check(request)
