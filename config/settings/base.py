import re
from datetime import timedelta
from pathlib import Path

import environ
from csp.constants import NONE, SELF, UNSAFE_INLINE
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# Secure by default: everything below that differs between dev and prod keys off DEBUG.
DEBUG = env.bool("DEBUG", default=False)
PRODUCTION = not DEBUG

SECRET_KEY = env("SECRET_KEY", default="dev-insecure-change-me" if DEBUG else None)
if SECRET_KEY is None:
    raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is False.")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"] if DEBUG else [])
if PRODUCTION and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set when DEBUG is False.")

INSTALLED_APPS = [
    "axes",
    "csp",
    "jazzmin",  # must precede django.contrib.admin
    "nested_admin",
    "adminsortable2",
    "tinymce",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",  # only for its sitemap.xml template; links come from SITE_URL
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.wishlist",
    "apps.cart",
    "apps.orders",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "apps.core.middleware.AdminCSPRelaxMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.TwoFactorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",  # keep last
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.storefront",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["axes.backends.AxesStandaloneBackend", "django.contrib.auth.backends.ModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "catalog:home"
LOGOUT_REDIRECT_URL = "catalog:home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles"))  # production: shared dir so old hashed files survive a deploy
MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))  # production: a directory shared between releases
# CSV photo imports: uploaded ZIPs wait here (NOT inside MEDIA_ROOT, which Nginx serves publicly)
IMPORT_TMP_DIR = env("IMPORT_TMP_DIR", default=str(Path(MEDIA_ROOT).parent / "import_tmp"))
IMAGE_IMPORT_ASYNC = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_NAME = "NarizMart"

# Where the admin lives. Obscurity is only a speed bump (the real protection is the emailed sign-in code, lockouts and rate
# limits), but a non-default path keeps bots that probe /admin/ away. Set ADMIN_URL=manage-xxxx/ in the server's .env.
ADMIN_URL = env("ADMIN_URL", default="admin/").strip().strip("/") + "/"
if not re.fullmatch(r"[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*/", ADMIN_URL) or any(set(part) == {"."} for part in ADMIN_URL.split("/") if part):
    raise ImproperlyConfigured("ADMIN_URL may only contain letters, digits, . _ ~ - and / between parts, e.g. manage-k7x2p9/")
APP_RELEASE = env("APP_RELEASE", default="dev")  # human label: a version tag such as v1.2.0, or v1.2.0-3-g6476069 / a short commit id
APP_VERSION = env("APP_VERSION", default="dev")  # set to the git SHA by the deploy workflow; changes the service worker version
SITE_URL = env("SITE_URL", default="http://localhost:8000")  # absolute links in emails sent outside a request
SUPPORT_EMAIL = env("SUPPORT_EMAIL", default="hello@narizmart.com")  # contact-form messages are delivered here
# Console in dev, real SMTP when DEBUG is False (override with EMAIL_BACKEND).
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
# Resend (https://resend.com): set RESEND_API_KEY and SMTP is configured for you (user is literally "resend").
# Any other SMTP provider still works by setting EMAIL_HOST / EMAIL_HOST_USER / EMAIL_HOST_PASSWORD explicitly.
RESEND_API_KEY = env("RESEND_API_KEY", default="")
EMAIL_HOST = env("EMAIL_HOST", default="smtp.resend.com" if RESEND_API_KEY else "localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="resend" if RESEND_API_KEY else "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default=RESEND_API_KEY)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="NarizMart <no-reply@narizmart.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# --- authentication hardening ---
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24  # reset / set-password links: single-use, valid 24 hours
EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24 * 3  # verification links valid for 3 days
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Trust X-Forwarded-For only in production, where Nginx is the sole proxy in front of the app.
USE_X_FORWARDED_FOR = env.bool("USE_X_FORWARDED_FOR", default=PRODUCTION)

# --- brute-force protection (django-axes): lock username+IP after repeated failures ---
AXES_ENABLED = True
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]  # IP alone would punish shared mobile/CGNAT addresses
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "apps.core.ip.client_ip"
AXES_USERNAME_FORM_FIELD = "username"
AXES_USERNAME_CALLABLE = "apps.core.ip.axes_username"
AXES_LOCKOUT_TEMPLATE = "429.html"
AXES_HTTP_RESPONSE_CODE = 429
AXES_VERBOSE = False

# --- request rate limiting (django-ratelimit) ---
RATELIMIT_IP_META_KEY = "apps.core.ip.client_ip"
RATELIMIT_USE_CACHE = "default"
if DEBUG:
    SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003", "django_ratelimit.W001"]  # locmem cache is fine locally

# Rich text editor (admin only). Output is sanitised by apps.core.richtext, so this list is a convenience, not the security boundary.
TINYMCE_DEFAULT_CONFIG = {
    "license_key": "gpl",  # free, self-hosted edition (GPL); no account or paid key needed
    "height": 320,
    "menubar": False,
    "branding": False,
    "promotion": False,
    "statusbar": False,
    "plugins": "link lists autoresize",
    "toolbar": "blocks | bold italic underline | bullist numlist | link blockquote hr | removeformat | undo redo",
    "block_formats": "Paragraph=p; Heading=h2; Subheading=h3",
    "relative_urls": False,
    "convert_urls": False,
    "paste_as_text": False,
    "link_default_target": "",
    "target_list": False,
    "valid_elements": "p,br,strong/b,em/i,u,s,h2,h3,h4,ul,ol,li,blockquote,hr,a[href|title]",
    "content_style": "body{font-family:DM Sans,Arial,sans-serif;font-size:14px;line-height:1.6}",
}

# --- Content-Security-Policy (django-csp) ---
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [SELF],
        "script-src": [SELF],
        "style-src": [SELF, UNSAFE_INLINE],  # templates use style="" for per-product colours
        "img-src": [SELF, "data:", "blob:"],
        "font-src": [SELF, "data:"],
        "connect-src": [SELF],
        "manifest-src": [SELF],
        "worker-src": [SELF],
        "object-src": [NONE],
        "base-uri": [SELF],
        "form-action": [SELF],
        "frame-ancestors": [NONE],
        **({"upgrade-insecure-requests": True} if PRODUCTION else {}),
    }
}
CACHES = (
    {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}
    if env("REDIS_URL", default="")
    else {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)

# Currency and delivery pricing live in the database: Admin -> Site settings (core.SiteSettings).

# --- production-only hardening (active whenever DEBUG is False) ---
if PRODUCTION:
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "config.storage.DirectoryTolerantManifestStorage"
    },
}

# --- Admin UI (Jazzmin) ---
JAZZMIN_SETTINGS = {
    "site_title": "NarizMart Admin",
    "site_header": "NarizMart",
    "site_brand": "NarizMart",
    "site_logo": "img/logo.png",
    "login_logo": "img/logo.png",
    "site_logo_classes": "img-fluid",
    "welcome_sign": "Sign in to manage NarizMart",
    "copyright": f"NarizMart {APP_RELEASE}",
    "search_model": ["orders.Order", "catalog.Product", "accounts.User"],
    "user_avatar": None,
    "topmenu_links": [
        {"name": "Dashboard", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "View store", "url": "/", "new_window": True},
        {"model": "orders.Order"},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "order_with_respect_to": ["orders", "catalog", "accounts", "core", "wishlist", "cart"],
    "icons": {
        "accounts.User": "fas fa-users",
        "auth.Group": "fas fa-user-shield",
        "orders.Order": "fas fa-receipt",
        "orders.OrderItem": "fas fa-box",
        "orders.OrderStatusHistory": "fas fa-stream",
        "catalog.Product": "fas fa-tags",
        "catalog.Category": "fas fa-sitemap",
        "core.SiteSettings": "fas fa-sliders-h",
        "core.Benefit": "fas fa-award",
        "core.NewsletterSubscriber": "fas fa-envelope-open-text",
        "core.Page": "fas fa-file-alt",
        "core.ContactMessage": "fas fa-inbox",
        "wishlist.WishlistItem": "fas fa-heart",
        "cart.Cart": "fas fa-shopping-bag",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {"auth.user": "collapsible", "auth.group": "vertical_tabs"},
    "custom_css": "css/admin.css",
    "use_google_fonts_cdn": False,  # no third-party requests; admin uses self-hosted DM Sans
}
JAZZMIN_UI_TWEAKS = {
    "navbar_small_text": False,
    "body_small_text": False,
    "brand_colour": "navbar-navy",
    "accent": "accent-teal",
    "navbar": "navbar-white navbar-light",
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_compact_style": False,
    "no_navbar_border": True,
    "sidebar": "sidebar-dark-teal",
    "sidebar_nav_child_indent": True,
    "sidebar_nav_flat_style": True,
    "theme": "default",
    "button_classes": {
        "primary": "btn-primary", "secondary": "btn-secondary", "info": "btn-info",
        "warning": "btn-warning", "danger": "btn-danger", "success": "btn-success",
    },
}

# --- logging: send errors (with tracebacks) to stderr, which systemd keeps in the journal -----------------------------------
# Without this, DEBUG=False hides every 500 traceback and failures can only be guessed at.
#   journalctl -u narizmart -n 100 --no-pager
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
