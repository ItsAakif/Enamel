"""Signed self-service links: view, cancel, reschedule, single use, cutoffs."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import SignedLinkUse
from apps.booking.services.booking import link_tokens_for
from apps.booking.services.links import PURPOSE_CANCEL, PURPOSE_RESCHEDULE, make_token
from apps.core.models import AuditEvent
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number
from apps.scheduling.models import Appointment

from .conftest import VALID_PHONE, utc_slot

pytestmark = pytest.mark.django_db


def link_url(token: str) -> str:
    return reverse("booking:public-link", args=[token])


def _appointment(seed, start, *, status="scheduled") -> Appointment:
    patient = Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Nina",
        last_name="New",
        date_of_birth="1990-05-04",
        phone_e164=VALID_PHONE,
    )
    return Appointment.all_objects.create(
        clinic=seed["clinic"],
        location=seed["location"],
        patient=patient,
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=status,
        source="online",
    )


def test_summary_is_masked(api_client, seed):
    appointment = _appointment(seed, utc_slot())
    tokens = link_tokens_for(appointment)

    response = api_client.get(link_url(tokens[PURPOSE_CANCEL]))

    assert response.status_code == 200
    body = response.json()
    assert body["appointment_id"] == str(appointment.id)
    assert body["provider_name"] == "Dr Public"
    assert body["allowed_actions"] == ["cancel"]
    assert body["patient"]["first_name"] != "Nina"
    assert body["patient"]["first_name"].startswith("N")
    assert body["patient"]["phone"] != VALID_PHONE
    assert body["patient"]["phone"].endswith("10")


def test_cancel_link_cancels_once_then_replays_are_410(api_client, seed):
    appointment = _appointment(seed, utc_slot())
    token = link_tokens_for(appointment)[PURPOSE_CANCEL]

    first = api_client.post(link_url(token), {"action": "cancel"}, format="json")
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"

    appointment.refresh_from_db()
    assert appointment.status == "cancelled"
    assert appointment.cancellation_reason == "cancelled_by_patient_link"
    assert AuditEvent.objects.filter(
        clinic=seed["clinic"], action="booking.cancelled"
    ).count() == 1
    assert SignedLinkUse.all_objects.filter(purpose="cancel").count() == 1

    second = api_client.post(link_url(token), {"action": "cancel"}, format="json")
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "invalid_token"


def test_tampered_token_is_410(api_client, seed):
    appointment = _appointment(seed, utc_slot())
    token = link_tokens_for(appointment)[PURPOSE_CANCEL]
    tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")

    response = api_client.get(link_url(tampered))

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "invalid_token"


def test_expired_token_is_410_expired(api_client, seed):
    appointment = _appointment(seed, utc_slot())
    token = make_token(appointment, PURPOSE_CANCEL, max_age_seconds=-60)

    response = api_client.get(link_url(token))

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "expired"


def test_cancel_inside_the_cutoff_is_409(api_client, seed):
    appointment = _appointment(seed, timezone.now() + timedelta(hours=1))
    token = link_tokens_for(appointment)[PURPOSE_CANCEL]

    response = api_client.post(link_url(token), {"action": "cancel"}, format="json")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "policy_cutoff"
    appointment.refresh_from_db()
    assert appointment.status == "scheduled"


def test_reschedule_link_moves_the_appointment(api_client, seed):
    appointment = _appointment(seed, utc_slot(hour=9))
    token = link_tokens_for(appointment)[PURPOSE_RESCHEDULE]

    response = api_client.post(
        link_url(token),
        {"action": "reschedule", "start_at": utc_slot(hour=11).isoformat()},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["rescheduled_from"] == str(appointment.id)

    appointment.refresh_from_db()
    assert appointment.status == "cancelled"

    new = Appointment.all_objects.get(pk=body["appointment_id"])
    assert new.rescheduled_from_id == appointment.id
    assert new.start_at == utc_slot(hour=11)
    assert AuditEvent.objects.filter(
        clinic=seed["clinic"], action="booking.rescheduled"
    ).count() == 1

    replay = api_client.post(
        link_url(token),
        {"action": "reschedule", "start_at": utc_slot(hour=13).isoformat()},
        format="json",
    )
    assert replay.status_code == 410
    assert replay.json()["error"]["code"] == "invalid_token"


def test_reschedule_disabled_by_policy_is_409(api_client, seed):
    from apps.booking.services.policy import get_policy

    policy = get_policy(seed["clinic"])
    policy.allow_online_reschedule = False
    policy.save(update_fields=["allow_online_reschedule", "updated_at"])

    appointment = _appointment(seed, utc_slot(hour=9))
    token = link_tokens_for(appointment)[PURPOSE_RESCHEDULE]

    response = api_client.post(
        link_url(token),
        {"action": "reschedule", "start_at": utc_slot(hour=11).isoformat()},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "policy_cutoff"


def test_wrong_action_for_the_token_purpose_is_410(api_client, seed):
    appointment = _appointment(seed, utc_slot())
    token = link_tokens_for(appointment)[PURPOSE_CANCEL]

    response = api_client.post(
        link_url(token),
        {"action": "reschedule", "start_at": utc_slot(hour=11).isoformat()},
        format="json",
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "invalid_token"
