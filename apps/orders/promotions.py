"""Discount rules: automatic promotions plus an optional coupon code. One discount per order, the best one for the customer."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.models import SiteSettings

from .models import Order, Promotion

COUPON_SESSION_KEY = "coupon_code"


@dataclass
class Evaluation:
    promotion: Promotion | None = None
    amount: Decimal = Decimal(0)
    coupon_code: str = ""        # the code that is actually being applied ("" when an automatic promotion won or no code)
    coupon_error: str = ""       # why a typed code can't be used (shown next to the code box)
    coupon_note: str = ""        # info, e.g. the automatic discount is already better
    nudge: str = ""              # "Spend X more to get 10% off"

    @property
    def label(self) -> str:
        return self.promotion.name if self.promotion else ""


def _money(amount: Decimal) -> str:
    return SiteSettings.load()._money(amount)   # display text only: the cached copy is fine


def _used(promotion: Promotion, user=None) -> int:
    qs = Order.objects.filter(promotion=promotion).exclude(status__in=Order.INACTIVE_STATUSES)
    if user is not None and getattr(user, "is_authenticated", False):
        qs = qs.filter(user=user)
    return qs.count()


def _limit_error(promotion: Promotion, user) -> str:
    if promotion.usage_limit is not None and _used(promotion) >= promotion.usage_limit:
        return "This offer has been fully claimed."
    if promotion.per_customer_limit is not None and getattr(user, "is_authenticated", False) and _used(promotion, user) >= promotion.per_customer_limit:
        return "You have already used this offer."
    return ""


def _eligibility_error(promotion: Promotion, subtotal: Decimal, user) -> str:
    if not promotion.is_active or not promotion.in_window():
        return "This code has expired." if promotion.is_active and promotion.ends_at and promotion.ends_at <= timezone.now() else "This code isn't valid right now."
    if subtotal < promotion.min_subtotal:
        return f"Add {_money(promotion.min_subtotal - subtotal)} more to use this code (minimum order {_money(promotion.min_subtotal)})."
    return _limit_error(promotion, user)


def normalise_code(raw: str) -> str:
    return " ".join((raw or "").split()).upper()[:30]


def evaluate_for_request(request, subtotal: Decimal, code: str = "") -> Evaluation:
    """Same as evaluate() for page rendering: the header, the cart drawer and the page itself ask for the same discount, so work it out once per request.
    (Placing an order never uses this: it re-checks everything inside its own transaction.)"""
    memo = request.__dict__.setdefault("_deal_memo", {})
    key = (str(subtotal), request.user.pk if request.user.is_authenticated else 0, code)
    if key not in memo:
        memo[key] = evaluate(subtotal, request.user, code)
    return memo[key]


def evaluate(subtotal: Decimal, user=None, code: str = "") -> Evaluation:
    """Work out the discount for this products total. Safe to call often; reads promotions fresh from the database."""
    result = Evaluation()
    now = timezone.now()
    live = Promotion.objects.filter(is_active=True).filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))

    best_auto: tuple[Decimal, Promotion] | None = None
    next_auto: Promotion | None = None
    for promo in live.filter(code=""):
        if subtotal < promo.min_subtotal:
            if promo.amount_for(promo.min_subtotal) > 0 and (next_auto is None or promo.min_subtotal < next_auto.min_subtotal):
                next_auto = promo
            continue
        if _limit_error(promo, user):
            continue
        amount = promo.amount_for(subtotal)
        if amount > 0 and (best_auto is None or amount > best_auto[0]):
            best_auto = (amount, promo)

    coupon: tuple[Decimal, Promotion] | None = None
    code = normalise_code(code)
    if code:
        promo = Promotion.objects.filter(code=code).first()
        if promo is None:
            result.coupon_error = "That code isn't valid."
        else:
            error = _eligibility_error(promo, subtotal, user)
            if error:
                result.coupon_error = error
            else:
                coupon = (promo.amount_for(subtotal), promo)

    if coupon and (best_auto is None or coupon[0] >= best_auto[0]):
        result.amount, result.promotion, result.coupon_code = coupon[0], coupon[1], code
    elif best_auto:
        result.amount, result.promotion = best_auto
        if coupon:
            result.coupon_note = f"Code {code} wasn't needed: “{best_auto[1].name}” already gives you a bigger discount."
    if result.promotion is None and next_auto is not None:
        gap = next_auto.min_subtotal - subtotal
        result.nudge = f"Add {_money(gap)} more to get {next_auto.name.rstrip('.')}."
    return result
