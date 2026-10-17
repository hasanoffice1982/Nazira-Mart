from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from django.views import View
from django.views.generic import DetailView, FormView, ListView

from apps.cart.services import delivery_charge, get_cart

from . import otp as otp_service
from .checkout import address_data, begin_confirmation, billing_for, save_new_shipping, complete_confirmation, needs_email_confirmation, pending_otp, send_order_confirmation
from .forms import CheckoutForm, OTPForm
from apps.core.models import SiteSettings
from apps.core.tracking import lines_event, order_event

from .models import Order
from .promotions import COUPON_SESSION_KEY, evaluate, normalise_code
from .services import OrderError, cancel_by_customer, create_order, lines_from_cart


@method_decorator(ratelimit(key="ip", rate="20/h", method="POST", block=True), name="post")
@method_decorator(ratelimit(key="post:email", rate="5/h", method="POST", block=True), name="post")
class CheckoutView(FormView):
    """Open to guests. Verified customers order directly; everyone else confirms by emailed code first."""

    template_name = "orders/checkout.html"
    form_class = CheckoutForm

    def dispatch(self, request, *args, **kwargs):
        if not get_cart(request).lines():
            messages.info(request, "Your bag is empty — add something first.")
            return redirect("catalog:shop")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        user = self.request.user
        if not user.is_authenticated:
            return {}
        return {"full_name": user.get_full_name() or user.first_name, "phone": user.phone}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cart = get_cart(self.request)
        subtotal = cart.subtotal
        deal = evaluate(subtotal, self.request.user, self.request.session.get(COUPON_SESSION_KEY, ""))
        ctx.update(
            track_data=lines_event("begin_checkout", cart.lines(), SiteSettings.load().currency_code),
            deal=deal, discount=deal.amount, payable=subtotal - deal.amount, coupon=self.request.session.get(COUPON_SESSION_KEY, ""),
            lines=cart.lines(), subtotal=subtotal, guest=needs_email_confirmation(self.request.user),
            charge_dhaka=delivery_charge(subtotal, "dhaka"), charge_outside=delivery_charge(subtotal, "outside"),
        )
        return ctx

    def form_valid(self, form):
        user, cart = self.request.user, get_cart(self.request)
        try:
            if needs_email_confirmation(user):
                email = user.email if user.is_authenticated else form.cleaned_data["email"]
                begin_confirmation(self.request, email, form.cleaned_data)
                return redirect("orders:confirm")
            data = address_data(form.cleaned_data)
            order = create_order(user=user, lines=lines_from_cart(cart), data=data, billing=billing_for(user, data), coupon_code=self.request.session.get(COUPON_SESSION_KEY, ""))
        except OrderError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        if form.cleaned_data.get("save_address") and not save_new_shipping(user, data):
            messages.info(self.request, "Your address book is full, so this address wasn't saved.")
        cart.clear()
        self.request.session.pop(COUPON_SESSION_KEY, None)
        send_order_confirmation(self.request, order)
        return redirect("orders:success", number=order.number)


@method_decorator(ratelimit(key="ip", rate="30/h", method="POST", block=True), name="post")
class ConfirmCheckoutView(FormView):
    template_name = "orders/confirm.html"
    form_class = OTPForm

    def dispatch(self, request, *args, **kwargs):
        self.otp = pending_otp(request)
        if self.otp is None:
            messages.info(request, "Your confirmation has expired. Please check out again.")
            return redirect("orders:checkout")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(masked_email=otp_service.mask_email(self.otp.email), total_lines=self.otp.payload["lines"])
        return ctx

    def form_valid(self, form):
        try:
            otp_service.verify(self.otp, form.cleaned_data["code"])
            order = complete_confirmation(self.request, self.otp)
        except (otp_service.OTPError, OrderError) as exc:
            form.add_error("code" if isinstance(exc, otp_service.OTPError) else None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Email confirmed — your order is placed!")
        return redirect("orders:success", number=order.number)


@method_decorator(ratelimit(key="ip", rate="10/h", method="POST", block=True), name="post")
class ResendCodeView(View):
    def post(self, request):
        otp = pending_otp(request)
        if otp is None:
            return redirect("orders:checkout")
        try:
            otp_service.resend(otp)
            messages.success(request, f"A new code was sent to {otp_service.mask_email(otp.email)}.")
        except otp_service.OTPError as exc:
            messages.error(request, str(exc))
        return redirect("orders:confirm")


class OwnedOrderMixin(LoginRequiredMixin):
    def get_queryset(self):
        return Order.objects.filter(user=self.request.user).prefetch_related("items", "history")


class OrderListView(OwnedOrderMixin, ListView):
    template_name = "orders/list.html"
    context_object_name = "orders"
    paginate_by = 10


class OrderDetailView(OwnedOrderMixin, DetailView):
    template_name = "orders/detail.html"
    slug_field = "number"
    slug_url_kwarg = "number"
    context_object_name = "order"


class OrderSuccessView(OrderDetailView):
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["track_data"] = order_event(self.object, SiteSettings.load().currency_code)
        return ctx

    template_name = "orders/success.html"


class CancelOrderView(OwnedOrderMixin, View):
    def post(self, request, number: str):
        order = get_object_or_404(self.get_queryset(), number=number)
        try:
            cancel_by_customer(order, request.user)
            messages.success(request, "Your order has been cancelled.")
        except OrderError as exc:
            messages.error(request, str(exc))
        return redirect(order)


@method_decorator(ratelimit(key="ip", rate="30/h", method="POST", block=True), name="post")
class ApplyCouponView(View):
    """Checks a typed code against the current bag; a valid code is remembered for this visit."""

    def post(self, request):
        code = normalise_code(request.POST.get("code", ""))
        cart = get_cart(request)
        if not code:
            request.session.pop(COUPON_SESSION_KEY, None)
            return redirect("orders:checkout")
        deal = evaluate(cart.subtotal, request.user, code)
        if deal.coupon_error:
            request.session.pop(COUPON_SESSION_KEY, None)
            messages.error(request, deal.coupon_error)
        else:
            request.session[COUPON_SESSION_KEY] = code
            messages.success(request, deal.coupon_note or f"Code {code} applied.")
        return redirect("orders:checkout")


class RemoveCouponView(View):
    def post(self, request):
        request.session.pop(COUPON_SESSION_KEY, None)
        messages.info(request, "Coupon removed.")
        return redirect("orders:checkout")
