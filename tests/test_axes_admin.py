import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.accounts.twofactor import VERIFIED_KEY

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name", ["accessattempt", "accesslog", "accessfailurelog"])
def test_axes_admin_pages_have_no_date_drilldown_so_they_work_on_mariadb_without_tz_tables(name):
    model_admin = next(a for m, a in admin.site._registry.items() if m._meta.model_name == name)
    assert model_admin.date_hierarchy is None


@pytest.mark.parametrize("name", ["accessattempt", "accesslog", "accessfailurelog"])
def test_axes_admin_pages_open(name):
    client = Client()
    client.force_login(get_user_model().objects.create_superuser("boss@example.com", "pass12345!x"))
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    assert client.get(reverse(f"admin:axes_{name}_changelist")).status_code == 200


def test_a_failing_axes_page_shows_the_error_to_superusers_only(monkeypatch):
    from axes.admin import AccessLogAdmin

    def boom(self, request, extra_context=None):
        raise RuntimeError("boom-from-test")

    monkeypatch.setattr(AccessLogAdmin, "changelist_view", boom)
    client = Client(raise_request_exception=False)
    client.force_login(get_user_model().objects.create_superuser("boss@example.com", "pass12345!x"))
    session = client.session
    session[VERIFIED_KEY] = True
    session.save()
    resp = client.get(reverse("admin:axes_accesslog_changelist"))
    assert resp.status_code == 500 and resp["Content-Type"].startswith("text/plain") and b"boom-from-test" in resp.content
