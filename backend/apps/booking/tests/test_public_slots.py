"""Public availability parity with the staff endpoint and input validation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db


def public_availability_url(slug: str) -> str:
    return reverse("booking:public-availability", args=[slug])


def test_public_and_staff_availability_are_identical(api_client, seed):
    from_date = timezone.localdate() + timedelta(days=1)
    to_date = from_date + timedelta(days=6)

    public = api_client.get(
        public_availability_url(seed["clinic"].slug),
        {
            "type": str(seed["appointment_type"].pk),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
    )
    assert public.status_code == 200

    api_client.force_login(seed["staff_user"])
    staff = api_client.get(
        reverse("scheduling:availability"),
        {
            "appointment_type": str(seed["appointment_type"].pk),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
        HTTP_X_CLINIC_ID=str(seed["clinic"].id),
    )
    assert staff.status_code == 200

    assert public.json() == staff.json()
    assert public.json()["slots"], "the seeded week should offer slots"


def test_slots_carry_no_patient_data(api_client, seed):
    from_date = timezone.localdate() + timedelta(days=1)

    body = api_client.get(
        public_availability_url(seed["clinic"].slug),
        {
            "type": str(seed["appointment_type"].pk),
            "from": from_date.isoformat(),
            "to": from_date.isoformat(),
        },
    ).json()

    for slot in body["slots"]:
        assert set(slot) == {
            "start",
            "end",
            "provider",
            "provider_name",
            "operatory",
            "operatory_name",
            "location",
            "reason",
        }
        assert "patient" not in slot


def test_unknown_type_is_404(api_client, seed):
    from_date = timezone.localdate() + timedelta(days=1)
    response = api_client.get(
        public_availability_url(seed["clinic"].slug),
        {
            "type": "00000000-0000-0000-0000-000000000000",
            "from": from_date.isoformat(),
            "to": from_date.isoformat(),
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_window_wider_than_31_days_is_400(api_client, seed):
    from_date = timezone.localdate() + timedelta(days=1)
    to_date = from_date + timedelta(days=40)

    response = api_client.get(
        public_availability_url(seed["clinic"].slug),
        {
            "type": str(seed["appointment_type"].pk),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_foreign_clinic_type_is_404(api_client, seed, clinic_factory):
    from apps.clinics.models import Location
    from apps.scheduling.models import AppointmentType

    other = clinic_factory(slug="foreign-clinic")
    Location.all_objects.create(clinic=other, name="Other")
    foreign_type = AppointmentType.all_objects.create(
        clinic=other, code="other", name="Other", duration_min=30, public_bookable=True
    )

    from_date = timezone.localdate() + timedelta(days=1)
    response = api_client.get(
        public_availability_url(seed["clinic"].slug),
        {
            "type": str(foreign_type.pk),
            "from": from_date.isoformat(),
            "to": from_date.isoformat(),
        },
    )

    assert response.status_code == 404
