"""Appointment reminder re-sequencing via the post_save signal (T114)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.clinics.models import Location, Provider
from apps.core.models import JobRequest
from apps.messaging.services import sequences
from apps.scheduling.models import Appointment, AppointmentType
from apps.scheduling.services.appointments import reschedule

from .conftest import DAYTIME, KOLKATA

pytestmark = pytest.mark.django_db


@pytest.fixture
def seed_scheduling(owner, patient_factory):
    location = Location.all_objects.create(clinic=owner.clinic, name="Main")
    provider = Provider.all_objects.create(
        clinic=owner.clinic, membership=owner.membership, display_name="Dr Owner"
    )
    appointment_type = AppointmentType.all_objects.create(
        clinic=owner.clinic, code="checkup", name="Checkup", duration_min=30
    )
    patient = patient_factory(owner.clinic)
    return {
        "clinic": owner.clinic,
        "location": location,
        "provider": provider,
        "appointment_type": appointment_type,
        "patient": patient,
    }


def _future_start(days: int = 10) -> datetime:
    base = timezone.now() + timedelta(days=days)
    return base.astimezone(KOLKATA).replace(hour=10, minute=0, second=0, microsecond=0)


def _make_appointment(seed, start, **extra) -> Appointment:
    return Appointment.all_objects.create(
        clinic=seed["clinic"],
        location=seed["location"],
        patient=seed["patient"],
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status="scheduled",
        **extra,
    )


def _pending(appointment) -> int:
    return JobRequest.objects.filter(
        kind=sequences.REMINDER_JOB,
        status=JobRequest.Status.PENDING,
        payload__appointment_id=str(appointment.id),
    ).count()


def test_booking_schedules_the_applicable_steps(seed_scheduling):
    appointment = _make_appointment(seed_scheduling, _future_start())

    assert _pending(appointment) == 3


def test_cancelling_removes_pending_steps(seed_scheduling):
    appointment = _make_appointment(seed_scheduling, _future_start())
    assert _pending(appointment) == 3

    appointment.status = "cancelled"
    appointment.save(update_fields=["status", "updated_at"])

    assert _pending(appointment) == 0


def test_reschedule_moves_the_steps(seed_scheduling):
    appointment = _make_appointment(seed_scheduling, _future_start())

    new = reschedule(appointment, _future_start(days=20))

    assert _pending(appointment) == 0
    assert _pending(new) == 3


def test_handle_reminder_sends_a_gated_message(
    seed_scheduling, templates, grant_consent
):
    templates(seed_scheduling["clinic"])
    grant_consent(seed_scheduling["patient"])
    appointment = _make_appointment(seed_scheduling, _future_start())

    job = (
        JobRequest.objects.filter(
            kind=sequences.REMINDER_JOB, payload__appointment_id=str(appointment.id)
        )
        .order_by("due_at")
        .first()
    )
    result = sequences.handle_reminder(dict(job.payload), now=DAYTIME)

    assert result["status"] == "sent"


def test_handle_reminder_skips_a_cancelled_appointment(seed_scheduling, templates):
    templates(seed_scheduling["clinic"])
    appointment = _make_appointment(seed_scheduling, _future_start())
    payload = {
        "appointment_id": str(appointment.id),
        "step": "t_minus_7",
        "channel": "email",
        "template_key": "appointment_reminder",
    }
    appointment.status = "cancelled"
    appointment.save(update_fields=["status", "updated_at"])

    assert sequences.handle_reminder(payload, now=DAYTIME)["skipped"] == "status"
