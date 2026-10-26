"""End-to-end public booking behaviour: create, replay, honeypot, holds."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import BookingConsent, BookingReference, ReservationHold
from apps.booking.services.holds import claim_hold, create_hold, expire_holds
from apps.booking.services.slots import public_slots
from apps.core.errors import ApiError
from apps.core.models import AuditEvent
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number
from apps.scheduling.models import Appointment
from apps.scheduling.status import STATUS_HELD

from .conftest import VALID_PHONE, booking_payload, utc_slot

pytestmark = pytest.mark.django_db

BOOKINGS_URL = reverse("booking:public-booking-create")


def test_booking_creates_reference_and_appointment(api_client, seed):
    slot = utc_slot()
    response = api_client.post(BOOKINGS_URL, booking_payload(seed, slot), format="json")

    assert response.status_code == 201
    body = response.json()
    assert body["reference"].startswith("BK-")
    assert len(body["reference"]) == 9
    from datetime import datetime

    assert datetime.fromisoformat(body["starts_at"]) == slot

    reference = BookingReference.all_objects.get(reference=body["reference"])
    appointment = reference.appointment
    assert str(appointment.id) == body["appointment_id"]
    assert appointment.status == "scheduled"
    assert appointment.source == "online"
    assert appointment.patient.source == Patient.Source.ONLINE

    consents = BookingConsent.all_objects.filter(reference=reference)
    assert {row.purpose for row in consents} == {"booking_terms", "privacy"}
    assert all(row.granted for row in consents)
    assert all(row.wording_version == "2026-01" for row in consents)
    assert all(row.captured_at is not None for row in consents)

    assert AuditEvent.objects.filter(clinic=seed["clinic"], action="booking.created").count() == 1


def test_idempotent_replay_returns_the_same_reference(api_client, seed):
    slot = utc_slot()
    payload = booking_payload(seed, slot, idempotency_key="replay-key-1")

    first = api_client.post(BOOKINGS_URL, payload, format="json")
    second = api_client.post(BOOKINGS_URL, payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["reference"] == second.json()["reference"]
    assert BookingReference.all_objects.filter(clinic=seed["clinic"]).count() == 1
    assert Appointment.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_same_key_with_a_different_slot_is_409(api_client, seed):
    payload = booking_payload(seed, utc_slot(), idempotency_key="mismatch-key")
    assert api_client.post(BOOKINGS_URL, payload, format="json").status_code == 201

    other = booking_payload(seed, utc_slot(hour=10), idempotency_key="mismatch-key")
    response = api_client.post(BOOKINGS_URL, other, format="json")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_mismatch"


def test_honeypot_is_a_silent_no_op(api_client, seed):
    payload = booking_payload(seed, utc_slot(), website="http://spam.example")

    response = api_client.post(BOOKINGS_URL, payload, format="json")

    assert response.status_code == 202
    assert BookingReference.all_objects.count() == 0
    assert Appointment.all_objects.count() == 0
    assert Patient.all_objects.count() == 0


def test_matching_patient_is_reused_not_duplicated(api_client, seed):
    existing = Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Nina",
        last_name="New",
        date_of_birth="1990-05-04",
        phone_e164=VALID_PHONE,
        source=Patient.Source.STAFF,
    )

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    reference = BookingReference.all_objects.get()
    assert reference.patient_id == existing.id
    # The existing staff record is not re-tagged as online.
    existing.refresh_from_db()
    assert existing.source == Patient.Source.STAFF
    assert Patient.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_partial_match_creates_a_provisional_patient(api_client, seed):
    Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Nina",
        last_name="New",
        date_of_birth="1980-01-01",  # same phone, different DOB
        phone_e164=VALID_PHONE,
        source=Patient.Source.STAFF,
    )

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    assert Patient.all_objects.filter(clinic=seed["clinic"]).count() == 2
    created = BookingReference.all_objects.get().patient
    assert created.source == Patient.Source.ONLINE


def test_patient_booking_limit_is_409(api_client, seed):
    patient = Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Nina",
        last_name="New",
        date_of_birth="1990-05-04",
        phone_e164=VALID_PHONE,
    )
    for index in range(5):
        start = utc_slot(days=10 + index)
        Appointment.all_objects.create(
            clinic=seed["clinic"],
            location=seed["location"],
            patient=patient,
            provider=seed["provider"],
            appointment_type=seed["appointment_type"],
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status="scheduled",
        )

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "patient_booking_limit"


def test_taken_slot_is_409_slot_conflict(api_client, seed):
    slot = utc_slot()
    first = api_client.post(
        BOOKINGS_URL,
        booking_payload(seed, slot, patient={
            "first_name": "A",
            "last_name": "One",
            "date_of_birth": "1990-05-04",
            "phone": "+919876500001",
        }),
        format="json",
    )
    assert first.status_code == 201

    second = api_client.post(
        BOOKINGS_URL,
        booking_payload(seed, slot, patient={
            "first_name": "B",
            "last_name": "Two",
            "date_of_birth": "1991-06-05",
            "phone": "+919876500002",
        }),
        format="json",
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "slot_conflict"


def test_slot_outside_the_window_is_409(api_client, seed):
    response = api_client.post(
        BOOKINGS_URL, booking_payload(seed, utc_slot(days=-1)), format="json"
    )

    assert response.status_code == 409


def _hold_for(seed, slot):
    patient = Patient.all_objects.create(
        clinic=seed["clinic"],
        number=allocate_patient_number(seed["clinic"]),
        first_name="Held",
        last_name="Patient",
        date_of_birth="1990-01-01",
        phone_e164="+919876500003",
    )
    return create_hold(
        seed["clinic"],
        provider=seed["provider"],
        appointment_type=seed["appointment_type"],
        location=seed["location"],
        patient=patient,
        slot_start=slot,
        phone_e164=patient.phone_e164,
    )


def test_hold_blocks_the_slot_then_expires_and_releases_it(api_client, seed):
    slot = utc_slot()
    hold = _hold_for(seed, slot)
    assert hold.status == ReservationHold.Status.ACTIVE

    day = slot.astimezone(timezone.get_current_timezone()).date()
    blocked = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert all(item["start"] != slot for item in blocked["slots"])

    # A **live** hold still blocks the slot: the submission is refused with
    # the generic slot conflict.
    live = api_client.post(BOOKINGS_URL, booking_payload(seed, slot), format="json")
    assert live.status_code == 409
    assert live.json()["error"]["code"] == "slot_conflict"

    # An **elapsed** hold does not: the availability already offers the slot
    # again (the hold is time-boxed), so the booking takes it (N-02). The
    # stale hold is released by the sweeper.
    ReservationHold.all_objects.filter(pk=hold.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    Appointment.all_objects.filter(pk=hold.appointment_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    late = api_client.post(BOOKINGS_URL, booking_payload(seed, slot), format="json")
    assert late.status_code == 201, late.content

    released = expire_holds()
    assert released["released"] >= 1
    assert released["expired_holds"] >= 1
    hold.refresh_from_db()
    assert hold.status == ReservationHold.Status.EXPIRED

    # The slot is taken by the new booking, not by the stale hold.
    booked = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert all(item["start"] != slot for item in booked["slots"])


def test_an_elapsed_hold_is_offered_again_and_can_be_booked(api_client, seed):
    """The availability and the booking agree about an elapsed hold (N-02)."""

    slot = utc_slot()
    hold = _hold_for(seed, slot)

    day = slot.astimezone(timezone.get_current_timezone()).date()
    ReservationHold.all_objects.filter(pk=hold.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    Appointment.all_objects.filter(pk=hold.appointment_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )

    # The availability offers the slot again ...
    offered = public_slots(seed["clinic"], seed["appointment_type"], day, day)
    assert any(item["start"] == slot for item in offered["slots"])

    # ... and the booking takes it: the elapsed hold is not a conflict.
    booked = api_client.post(BOOKINGS_URL, booking_payload(seed, slot), format="json")
    assert booked.status_code == 201, booked.content


def test_claiming_an_expired_hold_is_409_hold_expired(seed):
    slot = utc_slot()
    hold = _hold_for(seed, slot)
    ReservationHold.all_objects.filter(pk=hold.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    Appointment.all_objects.filter(pk=hold.appointment_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    hold.refresh_from_db()

    with pytest.raises(ApiError) as excinfo:
        claim_hold(hold)

    assert excinfo.value.code == "hold_expired"
    assert excinfo.value.status_code == 409
    hold.refresh_from_db()
    assert hold.status == ReservationHold.Status.EXPIRED


def test_created_response_carries_no_patient_data(api_client, seed):
    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    assert set(response.json()) == {"reference", "appointment_id", "starts_at"}


def test_email_verification_is_a_noop_when_not_required(api_client, seed):
    created = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")
    reference = created.json()["reference"]

    response = api_client.post(
        reverse("booking:public-booking-verify", args=[reference]),
        {"code": ""},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["email_verified"] is False


def test_email_verification_checks_the_code_when_required(api_client, seed):
    from apps.booking.services.booking import verification_code
    from apps.booking.services.policy import get_policy

    policy = get_policy(seed["clinic"])
    policy.require_email_verification = True
    policy.save(update_fields=["require_email_verification", "updated_at"])

    created = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")
    reference = created.json()["reference"]
    url = reverse("booking:public-booking-verify", args=[reference])

    wrong = api_client.post(url, {"code": "000000"}, format="json")
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "invalid_code"

    right = api_client.post(
        url, {"code": verification_code(reference)}, format="json"
    )
    assert right.status_code == 200
    assert right.json()["email_verified"] is True
    assert BookingReference.all_objects.get(reference=reference).email_verified_at is not None


def test_expired_hold_appointment_is_cancelled(seed):
    slot = utc_slot()
    hold = _hold_for(seed, slot)
    assert hold.appointment.status == STATUS_HELD

    expire_holds(now=timezone.now() + timedelta(hours=1))

    hold.appointment.refresh_from_db()
    assert hold.appointment.status == "cancelled"
    assert hold.appointment.cancellation_reason == "hold_expired"
