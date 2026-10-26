"""Patient dedup and provisional creation for public booking."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.booking.models import BookingReference
from apps.patients.models import Patient
from apps.patients.services.numbering import allocate_patient_number

from .conftest import VALID_PHONE, booking_payload, utc_slot

pytestmark = pytest.mark.django_db

BOOKINGS_URL = reverse("booking:public-booking-create")


def _patient(seed, **overrides):
    defaults = {
        "clinic": seed["clinic"],
        "number": allocate_patient_number(seed["clinic"]),
        "first_name": "Nina",
        "last_name": "New",
        "date_of_birth": "1990-05-04",
        "phone_e164": VALID_PHONE,
    }
    defaults.update(overrides)
    return Patient.all_objects.create(**defaults)


def test_phone_and_dob_match_links_to_the_existing_patient(api_client, seed):
    existing = _patient(seed, source=Patient.Source.STAFF)

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    assert BookingReference.all_objects.get().patient_id == existing.id
    assert Patient.all_objects.count() == 1


def test_no_match_creates_a_provisional_online_patient(api_client, seed):
    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    patient = BookingReference.all_objects.get().patient
    assert patient.source == Patient.Source.ONLINE
    assert patient.phone_e164 == VALID_PHONE
    assert patient.first_name == "Nina"


def test_other_clinic_patient_is_never_matched(api_client, seed, clinic_factory):
    other = clinic_factory(slug="dedup-other")
    Patient.all_objects.create(
        clinic=other,
        number=allocate_patient_number(other),
        first_name="Nina",
        last_name="New",
        date_of_birth="1990-05-04",
        phone_e164=VALID_PHONE,
    )

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    reference = BookingReference.all_objects.get()
    assert reference.clinic_id == seed["clinic"].id
    assert Patient.all_objects.filter(clinic=seed["clinic"]).count() == 1


def test_invalid_phone_is_a_400_field_error(api_client, seed):
    payload = booking_payload(seed, utc_slot())
    payload["patient"]["phone"] = "not-a-phone"

    response = api_client.post(BOOKINGS_URL, payload, format="json")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert Patient.all_objects.count() == 0


def test_archived_patient_is_not_matched(api_client, seed):
    from django.utils import timezone

    _patient(seed, deleted_at=timezone.now())

    response = api_client.post(BOOKINGS_URL, booking_payload(seed, utc_slot()), format="json")

    assert response.status_code == 201
    assert Patient.all_objects.filter(clinic=seed["clinic"]).count() == 2
