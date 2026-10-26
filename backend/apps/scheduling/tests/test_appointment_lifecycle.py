"""Appointment lifecycle: events, audit, transitions, holds, tenancy (T110)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.scheduling.models import Appointment, AppointmentEvent, AppointmentHistoryImmutableError
from apps.scheduling.services.appointments import expire_holds
from apps.scheduling.services.availability import compute_slots

from .conftest import utc

pytestmark = pytest.mark.django_db

MONDAY = utc(2026, 1, 5, 9, 0)
BEFORE_WINDOW = utc(2026, 1, 1, 0, 0)
APPOINTMENTS = "/api/v1/appointments/"


def _next_monday():
    """A Monday at least a week ahead, so lead time can never remove every slot."""

    today = timezone.localdate()
    delta = (0 - today.weekday()) % 7
    return today + timedelta(days=delta or 7)


def _payload(seed, start=MONDAY, **overrides) -> dict:
    payload = {
        "patient": str(seed["patient"].id),
        "provider": str(seed["provider"].id),
        "appointment_type": str(seed["appointment_type"].id),
        "location": str(seed["location"].id),
        "start_at": start if isinstance(start, str) else start.isoformat(),
    }
    payload.update(overrides)
    return payload


def _audit(clinic, action: str) -> int:
    return AuditEvent.objects.filter(
        clinic=clinic, entity_type="scheduling.appointment", action=action
    ).count()


def _events(appointment_id) -> list[str]:
    return list(
        AppointmentEvent.all_objects.filter(appointment_id=appointment_id)
        .order_by("at", "id")
        .values_list("event_type", flat=True)
    )


def test_create_writes_event_and_audit(owner, seed_scheduling):
    seed = seed_scheduling()

    response = owner.post(APPOINTMENTS, _payload(seed))

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "scheduled"
    assert body["version"] == 1
    assert _events(body["id"]) == ["created"]
    assert _audit(seed["clinic"], "appointment.created") == 1


def test_every_transition_writes_event_and_audit(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]

    for action, expected in (
        ("confirm", "confirmed"),
        ("check-in", "checked_in"),
        ("start", "in_progress"),
        ("complete", "completed"),
    ):
        response = owner.post(f"{APPOINTMENTS}{appointment_id}/{action}/")
        assert response.status_code == 200, (action, response.json())
        assert response.json()["status"] == expected

    assert _events(appointment_id) == [
        "created",
        "status_changed",
        "status_changed",
        "status_changed",
        "status_changed",
    ]
    assert _audit(seed["clinic"], "appointment.status_changed") == 4
    assert Appointment.all_objects.get(pk=appointment_id).version == 5


def test_illegal_transition_writes_nothing(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]
    owner.post(f"{APPOINTMENTS}{appointment_id}/confirm/")
    owner.post(f"{APPOINTMENTS}{appointment_id}/check-in/")
    owner.post(f"{APPOINTMENTS}{appointment_id}/complete/")

    events_before = AppointmentEvent.all_objects.filter(appointment_id=appointment_id).count()
    audit_before = AuditEvent.objects.filter(clinic=seed["clinic"]).count()

    response = owner.post(f"{APPOINTMENTS}{appointment_id}/confirm/")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"]["from"] == "completed"
    assert error["details"]["to"] == "confirmed"

    assert (
        AppointmentEvent.all_objects.filter(appointment_id=appointment_id).count()
        == events_before
    )
    assert AuditEvent.objects.filter(clinic=seed["clinic"]).count() == audit_before
    assert Appointment.all_objects.get(pk=appointment_id).status == "completed"


def test_cancel_records_reason_and_audits(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]

    response = owner.post(
        f"{APPOINTMENTS}{appointment_id}/cancel/", {"reason": "Patient unwell"}
    )

    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancellation_reason"] == "Patient unwell"
    assert _events(appointment_id) == ["created", "cancelled"]
    assert _audit(seed["clinic"], "appointment.cancelled") == 1


def test_no_show_transition(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]
    owner.post(f"{APPOINTMENTS}{appointment_id}/confirm/")

    response = owner.post(f"{APPOINTMENTS}{appointment_id}/no-show/")

    assert response.status_code == 200
    assert response.json()["status"] == "no_show"


def test_hold_expires_and_releases_the_slot(owner, seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )

    response = owner.post(APPOINTMENTS, _payload(seed, status="held"))
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "held"
    assert body["hold_expires_at"] is not None

    held = Appointment.all_objects.get(pk=body["id"])
    assert held.hold_expires_at <= timezone.now() + timedelta(minutes=10, seconds=5)

    blocked = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY.date(), MONDAY.date(), now=BEFORE_WINDOW
    )
    assert MONDAY not in [slot["start"] for slot in blocked["slots"]]

    released = expire_holds(now=held.hold_expires_at + timedelta(seconds=1))
    assert released == 1
    held.refresh_from_db()
    assert held.status == "cancelled"
    assert held.cancellation_reason == "hold_expired"
    assert _events(held.pk) == ["created", "cancelled"]

    free = compute_slots(
        seed["clinic"], seed["appointment_type"], MONDAY.date(), MONDAY.date(), now=BEFORE_WINDOW
    )
    assert MONDAY in [slot["start"] for slot in free["slots"]]


def test_expired_hold_is_not_released_early(owner, seed_scheduling):
    seed = seed_scheduling()
    owner.post(APPOINTMENTS, _payload(seed, status="held"))
    assert expire_holds(now=timezone.now()) == 0


def test_end_at_must_match_the_type_duration(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    response = owner.post(
        APPOINTMENTS,
        _payload(seed, end_at=(MONDAY + timedelta(minutes=45)).isoformat()),
    )
    assert response.status_code == 400
    assert "end_at" in response.json()["error"]["field_errors"]


def test_end_before_start_is_rejected(owner, seed_scheduling):
    seed = seed_scheduling(duration_min=30)
    response = owner.post(
        APPOINTMENTS,
        _payload(seed, end_at=(MONDAY - timedelta(minutes=30)).isoformat()),
    )
    assert response.status_code == 400


def test_naive_start_at_is_rejected(owner, seed_scheduling):
    seed = seed_scheduling()
    response = owner.post(APPOINTMENTS, _payload(seed, start_at="2026-01-05T09:00:00"))
    assert response.status_code == 400
    assert "start_at" in response.json()["error"]["field_errors"]


def test_date_only_bounds_cover_the_whole_day(owner, seed_scheduling):
    """``from``/``to`` as plain dates must include every appointment of that day.

    ``django.utils.dateparse.parse_datetime`` accepts a bare date and resolves
    it to midnight, so a ``to=<date>`` bound used to exclude the whole day it
    named.
    """

    seed = seed_scheduling()
    day = _next_monday()
    created = owner.post(
        APPOINTMENTS,
        _payload(seed, utc(day.year, day.month, day.day, 9, 0)),
    ).json()

    response = owner.get(
        APPOINTMENTS, {"from": day.isoformat(), "to": day.isoformat()}
    )

    assert response.status_code == 200, response.json()
    assert [row["id"] for row in response.json()["results"]] == [created["id"]]


def test_explicit_datetime_bounds_are_honoured(owner, seed_scheduling):
    """A datetime bound still means that instant, not the whole day."""

    seed = seed_scheduling()
    day = _next_monday()
    morning = utc(day.year, day.month, day.day, 9, 0)
    evening = utc(day.year, day.month, day.day, 18, 0)
    created = owner.post(APPOINTMENTS, _payload(seed, morning)).json()

    response = owner.get(
        APPOINTMENTS,
        {"from": morning.isoformat(), "to": evening.isoformat()},
    )

    assert response.status_code == 200, response.json()
    assert [row["id"] for row in response.json()["results"]] == [created["id"]]

    before = owner.get(APPOINTMENTS, {"to": morning.isoformat()})
    assert before.status_code == 200, before.json()
    assert before.json()["count"] == 0


def test_cross_clinic_appointment_is_404(owner, staff_factory, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]
    other = staff_factory(role="owner")

    detail = other.get(f"{APPOINTMENTS}{appointment_id}/")
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "not_found"

    cancelled = other.post(f"{APPOINTMENTS}{appointment_id}/cancel/", {})
    assert cancelled.status_code == 404

    listed = other.get(APPOINTMENTS)
    assert listed.json()["count"] == 0


def test_foreign_patient_is_rejected(owner, staff_factory, seed_scheduling, patient_factory):
    seed = seed_scheduling()
    other = staff_factory(role="owner")
    foreign_patient = patient_factory(other.clinic)

    response = owner.post(
        APPOINTMENTS, _payload(seed, patient=str(foreign_patient.id))
    )
    assert response.status_code == 400
    assert "patient" in response.json()["error"]["field_errors"]


def test_patch_updates_reason_and_version(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]

    response = owner.patch(
        f"{APPOINTMENTS}{appointment_id}/", {"reason": "Follow-up", "notes": "Bring X-ray"}
    )

    assert response.status_code == 200, response.json()
    assert response.json()["reason"] == "Follow-up"
    assert response.json()["version"] == 2
    assert _events(appointment_id) == ["created", "updated"]
    assert _audit(seed["clinic"], "appointment.updated") == 1


def test_patch_with_stale_version_is_a_409(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]
    owner.patch(f"{APPOINTMENTS}{appointment_id}/", {"reason": "First"})

    response = owner.patch(
        f"{APPOINTMENTS}{appointment_id}/", {"reason": "Second", "version": 1}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"


def test_idempotent_create_replays_the_same_appointment(owner, seed_scheduling):
    seed = seed_scheduling()
    payload = _payload(seed, idempotency_key="key-1")

    first = owner.post(APPOINTMENTS, payload)
    second = owner.post(APPOINTMENTS, payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_idempotency_key_with_different_body_is_a_409(owner, seed_scheduling):
    seed = seed_scheduling()
    owner.post(APPOINTMENTS, _payload(seed, idempotency_key="key-2"))

    response = owner.post(
        APPOINTMENTS,
        _payload(seed, start=(MONDAY + timedelta(hours=1)).isoformat(), idempotency_key="key-2"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_mismatch"


def test_appointment_event_is_append_only(owner, seed_scheduling):
    seed = seed_scheduling()
    appointment_id = owner.post(APPOINTMENTS, _payload(seed)).json()["id"]
    event = AppointmentEvent.all_objects.get(appointment_id=appointment_id)

    event.event_type = "updated"
    with pytest.raises(AppointmentHistoryImmutableError):
        event.save()
    with pytest.raises(AppointmentHistoryImmutableError):
        event.delete()


def test_assistant_can_read_but_not_manage(assistant, seed_scheduling):
    seed = seed_scheduling()
    assert assistant.get(APPOINTMENTS).status_code == 200

    created = assistant.post(APPOINTMENTS, _payload(seed))
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "permission_denied"

    types = assistant.get("/api/v1/appointment-types/")
    assert types.status_code == 403


def test_availability_endpoint_returns_slots(owner, seed_scheduling, rule_factory):
    seed = seed_scheduling(duration_min=30)
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )
    monday = _next_monday()

    response = owner.get(
        "/api/v1/availability/",
        {
            "appointment_type": str(seed["appointment_type"].id),
            "from": monday.isoformat(),
            "to": monday.isoformat(),
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["window"]["slot_count"] == 7
    assert len(body["slots"]) == 7
    assert body["slots"][0]["provider"] == str(seed["provider"].id)
    assert body["slots"][0]["reason"] == "available"


def test_availability_rejects_a_window_wider_than_31_days(owner, seed_scheduling):
    seed = seed_scheduling()
    response = owner.get(
        "/api/v1/availability/",
        {
            "appointment_type": str(seed["appointment_type"].id),
            "from": "2026-01-01",
            "to": "2026-02-01",
        },
    )
    assert response.status_code == 400
    assert "to" in response.json()["error"]["field_errors"]


def test_availability_foreign_type_is_404(owner, staff_factory, seed_scheduling):
    seed = seed_scheduling()
    other = staff_factory(role="owner")
    own_types = owner.get("/api/v1/appointment-types/").json()["results"]
    assert [item["id"] for item in own_types] == [str(seed["appointment_type"].id)]
    monday = _next_monday()

    response = owner.get(
        "/api/v1/availability/",
        {
            "appointment_type": str(seed["appointment_type"].id),
            "from": monday.isoformat(),
            "to": monday.isoformat(),
        },
    )
    assert response.status_code == 200

    # A type that belongs to another clinic is indistinguishable from a missing one.
    from apps.scheduling.models import AppointmentType

    foreign = AppointmentType.all_objects.create(
        clinic=other.clinic, code="foreign", name="Foreign", duration_min=30
    )
    response = owner.get(
        "/api/v1/availability/",
        {
            "appointment_type": str(foreign.id),
            "from": monday.isoformat(),
            "to": monday.isoformat(),
        },
    )
    assert response.status_code == 404


def test_break_outside_a_working_rule_is_rejected(owner, seed_scheduling, rule_factory):
    seed = seed_scheduling()
    rule_factory(
        seed["clinic"], seed["location"], weekday=0, start_time="09:00", end_time="11:00"
    )

    outside = owner.post(
        "/api/v1/availability-rules/",
        {
            "location": str(seed["location"].id),
            "kind": "break",
            "weekday": 0,
            "start_time": "12:00",
            "end_time": "12:30",
        },
    )
    assert outside.status_code == 400
    assert "start_time" in outside.json()["error"]["field_errors"]

    inside = owner.post(
        "/api/v1/availability-rules/",
        {
            "location": str(seed["location"].id),
            "kind": "break",
            "weekday": 0,
            "start_time": "10:00",
            "end_time": "10:30",
        },
    )
    assert inside.status_code == 201, inside.json()


def test_rule_rejects_start_after_end(owner, seed_scheduling):
    seed = seed_scheduling()
    response = owner.post(
        "/api/v1/availability-rules/",
        {
            "location": str(seed["location"].id),
            "kind": "working",
            "weekday": 0,
            "start_time": "11:00",
            "end_time": "09:00",
        },
    )
    assert response.status_code == 400
    assert "end_time" in response.json()["error"]["field_errors"]


def test_exception_all_day_rejects_partial_times(owner, seed_scheduling):
    seed = seed_scheduling()
    response = owner.post(
        "/api/v1/exceptions/",
        {
            "location": str(seed["location"].id),
            "date": "2026-01-05",
            "all_day": True,
            "start_time": "09:00",
            "end_time": "10:00",
        },
    )
    assert response.status_code == 400
