"""Adapters and provider webhooks (T114 acceptance 7, 8)."""

from __future__ import annotations

import json

import pytest
from django.utils import timezone

from apps.messaging.adapters import (
    AdapterNotConfigured,
    DevAdapter,
    EmailAdapter,
    OutboundMessage,
    resolve_adapter,
)
from apps.messaging.adapters.sms import SmsAdapter
from apps.messaging.adapters.whatsapp import WhatsAppAdapter
from apps.messaging.models import Message, ProviderEvent
from apps.messaging.services import dispatch
from apps.messaging.services import webhooks as webhook_service

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db

REMINDER_CONTEXT = {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"}

OUTBOUND = OutboundMessage(
    id="abc",
    clinic_id="clinic",
    channel="email",
    purpose="transactional",
    to_address="ada@example.test",
    body="hello",
    subject="hi",
)


def test_dev_adapter_is_deterministic_and_offline():
    adapter = DevAdapter()
    assert adapter.send(OUTBOUND) == "dev-abc"


def test_resolve_adapter_defaults_to_dev(settings):
    settings.MESSAGING_ADAPTER = "dev"
    assert isinstance(resolve_adapter("email"), DevAdapter)
    assert isinstance(resolve_adapter("sms"), DevAdapter)


def test_sms_adapter_requires_credentials(settings):
    settings.TWILIO_ACCOUNT_SID = ""
    settings.TWILIO_AUTH_TOKEN = ""
    with pytest.raises(AdapterNotConfigured):
        SmsAdapter().send(OUTBOUND)


def test_whatsapp_adapter_requires_credentials(settings):
    settings.WHATSAPP_PHONE_NUMBER_ID = ""
    settings.WHATSAPP_ACCESS_TOKEN = ""
    with pytest.raises(AdapterNotConfigured):
        WhatsAppAdapter().send(OUTBOUND)


def test_email_adapter_uses_the_locmem_backend(mailoutbox):
    provider_id = EmailAdapter().send(OUTBOUND)
    assert provider_id == "email-abc"
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["ada@example.test"]


def test_resolve_adapter_rejects_unknown_channels(settings):
    settings.MESSAGING_ADAPTER = "smtp"
    with pytest.raises(AdapterNotConfigured):
        resolve_adapter("sms")


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


def _post_webhook(api_client, adapter, payload, secret, *, timestamp=None):
    body = json.dumps(payload).encode("utf-8")
    stamp = timestamp if timestamp is not None else str(int(timezone.now().timestamp()))
    signature = webhook_service.compute_signature(secret, stamp, body)
    return api_client.post(
        f"/api/v1/webhooks/messaging/{adapter}/",
        data=body,
        content_type="application/json",
        HTTP_X_MESSAGING_SIGNATURE=signature,
        HTTP_X_MESSAGING_TIMESTAMP=stamp,
    )


def _sent_message(owner, patient_factory, templates, grant_consent, key="webhook-1"):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)
    return dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        REMINDER_CONTEXT,
        idempotency_key=key,
        now=DAYTIME,
    )


def test_webhook_updates_status_idempotently(
    api_client, settings, owner, patient_factory, templates, grant_consent
):
    settings.MESSAGING_WEBHOOK_SECRET = "test-secret"
    message = _sent_message(owner, patient_factory, templates, grant_consent)
    payload = {
        "event_id": "evt-1",
        "provider_message_id": message.provider_message_id,
        "status": "delivered",
        "event_type": "delivery",
    }

    first = _post_webhook(api_client, "dev", payload, "test-secret")
    assert first.status_code == 200
    assert first.json()["status"] == "processed"

    second = _post_webhook(api_client, "dev", payload, "test-secret")
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    message.refresh_from_db()
    assert message.status == Message.Status.DELIVERED
    assert message.delivered_at is not None
    assert ProviderEvent.all_objects.filter(clinic=owner.clinic, event_id="evt-1").count() == 1


def test_webhook_ignores_unknown_message(api_client, settings):
    settings.MESSAGING_WEBHOOK_SECRET = "test-secret"
    payload = {"event_id": "evt-2", "provider_message_id": "nope", "status": "delivered"}

    response = _post_webhook(api_client, "dev", payload, "test-secret")

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_rejects_a_bad_signature(api_client, settings):
    settings.MESSAGING_WEBHOOK_SECRET = "test-secret"
    body = json.dumps({"event_id": "x"}).encode("utf-8")

    response = api_client.post(
        "/api/v1/webhooks/messaging/dev/",
        data=body,
        content_type="application/json",
        HTTP_X_MESSAGING_SIGNATURE="deadbeef",
        HTTP_X_MESSAGING_TIMESTAMP=str(int(timezone.now().timestamp())),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_signature"


def test_webhook_rejects_an_old_timestamp(api_client, settings):
    settings.MESSAGING_WEBHOOK_SECRET = "test-secret"
    old = str(int(timezone.now().timestamp()) - 100000)

    response = _post_webhook(
        api_client,
        "dev",
        {"event_id": "evt-old", "provider_message_id": "x"},
        "test-secret",
        timestamp=old,
    )

    assert response.status_code == 401


def test_webhook_rejects_unknown_adapter(api_client, settings):
    settings.MESSAGING_WEBHOOK_SECRET = "test-secret"
    response = _post_webhook(api_client, "carrier-pigeon", {"event_id": "x"}, "test-secret")
    assert response.status_code == 404
