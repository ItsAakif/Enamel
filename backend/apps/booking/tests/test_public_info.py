"""Public clinic payload: no PHI, only public types, slug-scoped."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.scheduling.models import AppointmentType

pytestmark = pytest.mark.django_db


def public_clinic_url(slug: str) -> str:
    return reverse("booking:public-clinic", args=[slug])


def test_public_payload_has_no_patient_data(api_client, seed):
    response = api_client.get(public_clinic_url(seed["clinic"].slug))

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()

    assert body["slug"] == seed["clinic"].slug
    assert "patient" not in body
    assert "patients" not in body
    # No patient-shaped keys anywhere in the serialized payload.
    serialized = str(body)
    for forbidden in ("patient_id", "first_name", "last_name", "date_of_birth"):
        assert forbidden not in serialized

    providers = body["providers"]
    assert providers == [
        {
            "id": str(seed["provider"].pk),
            "display_name": "Dr Public",
            "specialty": "General",
            "color": "#123456",
        }
    ]


def test_only_public_bookable_types_are_exposed(api_client, seed):
    AppointmentType.all_objects.create(
        clinic=seed["clinic"],
        code="internal",
        name="Internal review",
        duration_min=30,
        public_bookable=False,
        is_active=True,
    )

    body = api_client.get(public_clinic_url(seed["clinic"].slug)).json()

    codes = {item["code"] for item in body["appointment_types"]}
    assert codes == {"checkup"}


def test_policy_summary_is_returned(api_client, seed):
    body = api_client.get(public_clinic_url(seed["clinic"].slug)).json()

    policy = body["policy"]
    assert policy["lead_time_hours"] == 2
    assert policy["window_days"] == 31
    assert policy["hold_minutes"] == 10
    assert policy["cancel_cutoff_hours"] == 24
    assert policy["is_public_enabled"] is True


def test_unknown_slug_is_404(api_client):
    response = api_client.get(public_clinic_url("does-not-exist"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_slug_isolation_between_clinics(api_client, seed, clinic_factory):
    other = clinic_factory(slug="other-clinic")
    from apps.clinics.models import Location

    Location.all_objects.create(clinic=other, name="Other room")
    AppointmentType.all_objects.create(
        clinic=other,
        code="other-type",
        name="Other type",
        duration_min=30,
        public_bookable=True,
    )

    body = api_client.get(public_clinic_url(seed["clinic"].slug)).json()

    location_names = {item["name"] for item in body["locations"]}
    type_codes = {item["code"] for item in body["appointment_types"]}
    assert location_names == {"Main"}
    assert "other-type" not in type_codes
