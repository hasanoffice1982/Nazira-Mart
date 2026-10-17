from django.test import RequestFactory, override_settings

from apps.core.ip import FALLBACK, client_ip

rf = RequestFactory()


def req(**meta):
    r = rf.get("/")
    r.META.pop("REMOTE_ADDR", None)
    r.META.update(meta)
    return r


def test_uses_remote_addr_when_not_behind_a_proxy():
    assert client_ip(req(REMOTE_ADDR="203.0.113.7")) == "203.0.113.7"


def test_missing_or_garbage_remote_addr_falls_back_to_a_valid_ip():
    for meta in ({}, {"REMOTE_ADDR": ""}, {"REMOTE_ADDR": "unknown"}, {"REMOTE_ADDR": "/run/narizmart.sock"}):
        assert client_ip(req(**meta)) == FALLBACK


@override_settings(USE_X_FORWARDED_FOR=True)
def test_trusts_only_the_rightmost_forwarded_address():
    assert client_ip(req(HTTP_X_FORWARDED_FOR="6.6.6.6, 198.51.100.4", REMOTE_ADDR="")) == "198.51.100.4"


@override_settings(USE_X_FORWARDED_FOR=True)
def test_spoofed_garbage_header_is_ignored():
    assert client_ip(req(HTTP_X_FORWARDED_FOR="1.1.1.1, <script>", REMOTE_ADDR="10.0.0.9")) == "10.0.0.9"
    assert client_ip(req(HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="")) == FALLBACK


@override_settings(USE_X_FORWARDED_FOR=True)
def test_ipv6_is_normalised():
    assert client_ip(req(HTTP_X_FORWARDED_FOR="2001:DB8::1")) == "2001:db8::1"
