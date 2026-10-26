"""Conflict prevention: exclusion constraints, races, reschedule (T110)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import IntegrityError, connections
from django.utils import timezone

from apps.scheduling.models import Appointment, AppointmentEvent
from apps.scheduling.services.appointments import create_appointment

from .conftest import make_appointment, utc

pytestmark = pytest.mark.django_db

APPOINTMENTS = "/api/v1/appointments/"
START = utc(2026, 1, 5, 9, 0)


def _payload(seed, start, *, end=None, **overrides) -> dict:
    if end is None:
        end = start + timedelta(minutes=seed["appointment_type"].duration_min)
    payload = {
        "patient": str(seed["patient"].id),
        "provider": str(seed["provider"].id),
        "appointment_type": str(seed["appointment_type"].id),
        "location": str(seed["location"].id),
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
    }
    payload.update(overrides)
    return payload


def test_provider_overlap_hits_the_db_constraint(seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    make_appointment(seed, START, status="scheduled")

    with pytest.raises(IntegrityError) as excinfo:
        make_appointment(seed, START + timedelta(minutes=15), status="scheduled")

    assert "excl_appointment_provider_overlap" in str(excinfo.value)


def test_provider_overlap_maps_to_409_slot_conflict(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    first = owner.post(APPOINTMENTS, _payload(seed, START))
    assert first.status_code == 201, first.json()

    second = owner.post(APPOINTMENTS, _payload(seed, START + timedelta(minutes=15)))

    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "slot_conflict"
    assert error["details"]["appointment_id"] == first.json()["id"]
    assert "start_at" in error["field_errors"]
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_operatory_overlap_hits_the_db_constraint(
    seed_scheduling, operatory_factory, provider_factory, patient_factory
):
    seed = seed_scheduling(duration_min=30, requires_operatory=True)
    chair = operatory_factory(seed["clinic"], seed["location"], name="Chair 1")
    other_provider = provider_factory(seed["clinic"], display_name="Dr Other")
    other_patient = patient_factory(seed["clinic"])

    make_appointment(seed, START, operatory=chair, status="scheduled")

    with pytest.raises(IntegrityError) as excinfo:
        make_appointment(
            seed,
            START + timedelta(minutes=15),
            provider=other_provider,
            patient=other_patient,
            operatory=chair,
            status="scheduled",
        )
    assert "excl_appointment_operatory_overlap" in str(excinfo.value)


def test_operatory_overlap_maps_to_409_slot_conflict(
    owner, seed_scheduling, operatory_factory, provider_factory, patient_factory
):
    seed = seed_scheduling(duration_min=30, requires_operatory=True)
    chair = operatory_factory(seed["clinic"], seed["location"], name="Chair 1")
    other_provider = provider_factory(seed["clinic"], display_name="Dr Other")
    other_patient = patient_factory(seed["clinic"])

    first = owner.post(APPOINTMENTS, _payload(seed, START, operatory=str(chair.id)))
    assert first.status_code == 201, first.json()

    second = owner.post(
        APPOINTMENTS,
        _payload(
            seed,
            START + timedelta(minutes=15),
            operatory=str(chair.id),
            provider=str(other_provider.id),
            patient=str(other_patient.id),
        ),
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "slot_conflict"
    assert second.json()["error"]["details"]["operatory_id"] == str(chair.id)


def test_operatory_constraint_ignores_rows_without_an_operatory(seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    make_appointment(seed, START, operatory=None, status="scheduled")
    # A second appointment with no operatory at all must not trip the chair rule.
    make_appointment(seed, START + timedelta(hours=1), operatory=None, status="scheduled")
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 2


def test_adjacent_appointments_are_allowed(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    first = owner.post(APPOINTMENTS, _payload(seed, START))
    assert first.status_code == 201, first.json()

    # Half-open ranges: 09:00-09:30 and 09:30-10:00 do not overlap.
    second = owner.post(APPOINTMENTS, _payload(seed, START + timedelta(minutes=30)))
    assert second.status_code == 201, second.json()


def test_cancelled_appointment_releases_the_range(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    first = owner.post(APPOINTMENTS, _payload(seed, START)).json()
    owner.post(f"{APPOINTMENTS}{first['id']}/cancel/", {"reason": "changed"})

    second = owner.post(APPOINTMENTS, _payload(seed, START))
    assert second.status_code == 201, second.json()


@pytest.mark.django_db(transaction=True)
def test_parallel_booking_of_one_slot_has_exactly_one_winner(owner, seed_scheduling):
    from rest_framework.test import APIClient

    seed = seed_scheduling(duration_min=30)
    url = APPOINTMENTS
    clinic_id = str(owner.clinic.id)
    payload = _payload(seed, START)

    clients = [APIClient(), APIClient()]
    for client in clients:
        client.force_login(owner.user)

    def send(client):
        connections.close_all()
        try:
            response = client.post(
                url, payload, format="json", HTTP_X_CLINIC_ID=clinic_id
            )
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, client) for client in clients]
        results = [future.result(timeout=30) for future in futures]

    statuses = sorted(status for status, _ in results)
    assert statuses == [201, 409], results

    conflict = next(body for status, body in results if status == 409)
    assert conflict["error"]["code"] == "slot_conflict"

    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 1
    assert AppointmentEvent.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_reschedule_creates_a_linked_appointment(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    original = owner.post(APPOINTMENTS, _payload(seed, START)).json()

    response = owner.post(
        f"{APPOINTMENTS}{original['id']}/reschedule/",
        {"start_at": (START + timedelta(days=1)).isoformat(), "reason": "patient request"},
    )

    assert response.status_code == 201, response.json()
    moved = response.json()
    assert moved["id"] != original["id"]
    assert moved["rescheduled_from"] == original["id"]
    assert moved["status"] == "scheduled"
    assert moved["version"] == 1

    old = Appointment.all_objects.get(pk=original["id"])
    assert old.status == "cancelled"
    assert old.cancellation_reason == "rescheduled"

    assert list(
        AppointmentEvent.all_objects.filter(appointment_id=original["id"])
        .order_by("at", "id")
        .values_list("event_type", flat=True)
    ) == ["created", "cancelled", "rescheduled"]
    assert list(
        AppointmentEvent.all_objects.filter(appointment_id=moved["id"]).values_list(
            "event_type", flat=True
        )
    ) == ["created"]

    from apps.core.models import AuditEvent

    assert (
        AuditEvent.objects.filter(
            clinic=seed["clinic"], action="appointment.rescheduled"
        ).count()
        == 1
    )


def test_reschedule_frees_the_original_slot(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    original = owner.post(APPOINTMENTS, _payload(seed, START)).json()
    owner.post(
        f"{APPOINTMENTS}{original['id']}/reschedule/",
        {"start_at": (START + timedelta(days=1)).isoformat()},
    )

    # The freed range can be booked again.
    rebooked = owner.post(APPOINTMENTS, _payload(seed, START))
    assert rebooked.status_code == 201, rebooked.json()


def test_reschedule_into_a_taken_slot_rolls_back(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    original = owner.post(APPOINTMENTS, _payload(seed, START)).json()
    blocker_start = START + timedelta(days=1)
    owner.post(APPOINTMENTS, _payload(seed, blocker_start))

    response = owner.post(
        f"{APPOINTMENTS}{original['id']}/reschedule/",
        {"start_at": blocker_start.isoformat()},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_conflict"

    # Nothing was written: the original is still scheduled and no successor exists.
    old = Appointment.all_objects.get(pk=original["id"])
    assert old.status == "scheduled"
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 2


def test_service_level_create_maps_conflicts_without_a_500(seed_scheduling):
    from apps.core.errors import ApiError

    seed = seed_scheduling(duration_min=30)
    create_appointment(
        seed["clinic"],
        patient=seed["patient"],
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        location=seed["location"],
        start_at=START,
    )

    with pytest.raises(ApiError) as excinfo:
        create_appointment(
            seed["clinic"],
            patient=seed["patient"],
            provider=seed["provider"],
            appointment_type=seed["appointment_type"],
            location=seed["location"],
            start_at=START + timedelta(minutes=10),
        )

    assert excinfo.value.code == "slot_conflict"
    assert excinfo.value.status_code == 409
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_hold_blocks_a_second_claim(seed_scheduling):
    from apps.core.errors import ApiError

    seed = seed_scheduling(duration_min=30)
    create_appointment(
        seed["clinic"],
        patient=seed["patient"],
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        location=seed["location"],
        start_at=START,
        status="held",
    )

    with pytest.raises(ApiError) as excinfo:
        create_appointment(
            seed["clinic"],
            patient=seed["patient"],
            provider=seed["provider"],
            appointment_type=seed["appointment_type"],
            location=seed["location"],
            start_at=START,
        )
    assert excinfo.value.code == "slot_conflict"


def test_version_is_bumped_once_per_transition(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    appointment_id = owner.post(APPOINTMENTS, _payload(seed, START)).json()["id"]
    owner.post(f"{APPOINTMENTS}{appointment_id}/confirm/")
    owner.post(f"{APPOINTMENTS}{appointment_id}/check-in/")

    appointment = Appointment.all_objects.get(pk=appointment_id)
    assert appointment.status == "checked_in"
    assert appointment.version == 3
    assert appointment.updated_at >= appointment.created_at


def test_timezone_utc_storage(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    body = owner.post(APPOINTMENTS, _payload(seed, START)).json()
    appointment = Appointment.all_objects.get(pk=body["id"])
    assert timezone.is_aware(appointment.start_at)
    assert appointment.start_at.utcoffset() == timedelta(0)
    assert appointment.start_at == START
