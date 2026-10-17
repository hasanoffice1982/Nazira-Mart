"""Helpers for tests that post the Site settings admin form (it carries two inline blocks: hero slides and promo banners)."""
INLINE_PREFIXES = ("hero_slides", "promo_banners")


def inline_management(**counts) -> dict:
    data = {}
    for prefix in INLINE_PREFIXES:
        total, initial = counts.get(prefix, (0, 0))
        data.update({f"{prefix}-TOTAL_FORMS": str(total), f"{prefix}-INITIAL_FORMS": str(initial), f"{prefix}-MIN_NUM_FORMS": "0", f"{prefix}-MAX_NUM_FORMS": "1000"})
    return data
