import pytest
from django.core.cache import cache
from django.core import mail
from django.urls import reverse

from apps.core.models import ContactMessage

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    cache.clear()
GOOD = {"name": "Md Hasan", "email": "Dr.Has82@Gmail.com", "phone": "01712062236", "order_number": "bm-1a2b3c4d", "message": "Where is my parcel, please check?"}


def test_form_page_is_empty_and_has_a_roomy_message_box(client):
    html = client.get(reverse("core:contact")).content.decode()
    assert 'name="message"' in html and 'rows="6"' in html and "hp_contact" in html


def test_submit_saves_emails_and_redirects_to_a_confirmation(client, settings):
    settings.SUPPORT_EMAIL = "hello@example.com"
    resp = client.post(reverse("core:contact"), GOOD)
    assert resp.status_code == 302 and resp["Location"] == reverse("core:contact_sent")
    msg = ContactMessage.objects.get()
    assert msg.email == "dr.has82@gmail.com" and msg.order_number == "BM-1A2B3C4D" and msg.message.startswith("Where is")
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["hello@example.com"] and mail.outbox[0].reply_to == ["dr.has82@gmail.com"]
    assert "Where is my parcel" in mail.outbox[0].body and "BM-1A2B3C4D" in mail.outbox[0].subject
    done = client.get(resp["Location"]).content.decode()
    assert "Message sent" in done and "Md Hasan" in done


def test_refresh_of_the_confirmation_does_not_resend_and_contact_page_is_clear_again(client):
    client.post(reverse("core:contact"), GOOD)
    sent = reverse("core:contact_sent")
    assert client.get(sent).status_code == 200 and client.get(sent).status_code == 200
    assert ContactMessage.objects.count() == 1 and len(mail.outbox) == 1
    empty = client.get(reverse("core:contact")).content.decode()
    assert "Where is my parcel" not in empty and "dr.has82" not in empty.lower()


def test_confirmation_without_a_submission_goes_back_to_the_form(client):
    resp = client.get(reverse("core:contact_sent"))
    assert resp.status_code == 302 and resp["Location"] == reverse("core:contact")


def test_invalid_submission_shows_errors_and_saves_nothing(client):
    resp = client.post(reverse("core:contact"), {**GOOD, "message": "short", "email": "nope"})
    assert resp.status_code == 200 and not ContactMessage.objects.exists() and not mail.outbox


def test_honeypot_and_email_failure_are_handled(client, monkeypatch):
    assert client.post(reverse("core:contact"), {**GOOD, "hp_contact": "spam"}).status_code == 200
    assert not ContactMessage.objects.exists()
    monkeypatch.setattr("django.core.mail.EmailMessage.send", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    resp = client.post(reverse("core:contact"), GOOD)
    assert resp.status_code == 302 and ContactMessage.objects.count() == 1  # saved even if the email failed
