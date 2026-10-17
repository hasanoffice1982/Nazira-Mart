from django.urls import path

from . import seo, views

app_name = "core"
urlpatterns = [
    path("p/<slug:slug>/", views.PageView.as_view(), name="page"),
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("contact/sent/", views.ContactSentView.as_view(), name="contact_sent"),
    path("healthz", views.healthz, name="healthz"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap"),
    path("robots.txt", seo.robots_txt, name="robots"),
    path("offline/", views.OfflineView.as_view(), name="offline"),
    path("manifest.webmanifest", views.ManifestView.as_view(), name="manifest"),
    path("newsletter/", views.NewsletterSubscribeView.as_view(), name="newsletter"),
    path("sw.js", views.ServiceWorkerView.as_view(), name="sw"),
]
