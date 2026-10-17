from django.conf import settings
from django.core.checks import Error, Warning, register


@register(deploy=True)
def production_config_checks(app_configs, **kwargs):
    issues = []
    smtp = settings.EMAIL_BACKEND.endswith("smtp.EmailBackend")
    if smtp and settings.EMAIL_HOST in ("", "localhost"):
        issues.append(Error("No email provider configured (set RESEND_API_KEY or EMAIL_HOST); sign-in codes, checkout codes and password resets will fail.", id="narizmart.E001"))
    if smtp and not settings.EMAIL_HOST_USER:
        issues.append(Warning("EMAIL_HOST_USER is empty; most SMTP providers require authentication.", id="narizmart.W001"))
    if settings.CACHES["default"]["BACKEND"].endswith("LocMemCache"):
        issues.append(Warning("REDIS_URL is not set: django-ratelimit counters are per-process, not shared across workers.", id="narizmart.W002"))
    if not settings.CSRF_TRUSTED_ORIGINS:
        issues.append(Warning("CSRF_TRUSTED_ORIGINS is empty; set it to your https origin(s).", id="narizmart.W003"))
    return issues
