"""Small cached lookups that every page needs (categories, footer pages, home banners).

Staff edits clear them straight away in the process that saved; other worker processes pick the change up within TTL seconds
(the same rule as Site settings, which is why the admin says changes can take up to 5 minutes)."""
from django.core.cache import cache

TTL = 300
KEYS = ("active_categories", "footer_pages", "home_hero_slides", "home_promo_banners", "home_benefits")


def cached(key: str, build, ttl: int = TTL):
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, ttl)
    return value


def clear(**kwargs) -> None:
    cache.delete_many(KEYS)


def connect() -> None:
    from django.apps import apps
    from django.db.models.signals import post_delete, post_save

    for label in ("catalog.Category", "core.Page", "core.HeroSlide", "core.PromoBanner", "core.Benefit"):
        model = apps.get_model(label)
        post_save.connect(clear, sender=model, dispatch_uid=f"cached-save-{label}")
        post_delete.connect(clear, sender=model, dispatch_uid=f"cached-del-{label}")
