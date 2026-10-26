"""FHIR facade validation and tenancy (T130)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.interop.fhir.mapping import (
    APPOINTMENT_IDENTIFIER_SYSTEM,
    PATIENT_IDENTIFIER_SYSTEM,
)
from apps.interop.fhir.resources import render_patient
from apps.interop.fhir.validate import FhirValidationError, validate_resource

pytestmark = pytest.mark.django_db


def _appointment(clinic, patient, deps, *, status="scheduled"):
    from django.utils import timezone

    from apps.scheduling.models import Appointment

    start = timezone.now()
    return Appointment.all_objects.create(
        clinic=clinic,
        location=deps["location"],
        patient=patient,
        provider=deps["provider"],
        appointment_type=deps["appointment_type"],
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=status,
    )


def _encounter(clinic, patient, *, notes="internal note"):
    from apps.patients.models import Encounter

    return Encounter.all_objects.create(
        clinic=clinic,
        patient=patient,
        kind="exam",
        status="finalized",
        chief_complaint="toothache",
        notes=notes,
    )


def test_patient_resource_validates_with_expected_identifier(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Grace", last_name="Hopper")
    response = owner.get(f"/api/v1/fhir/Patient/{patient.id}/")
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("application/fhir+json")

    body = response.json()
    assert body["resourceType"] == "Patient"
    assert body["id"] == str(patient.id)
    identifiers = {i["system"]: i["value"] for i in body["identifier"]}
    assert identifiers[PATIENT_IDENTIFIER_SYSTEM] == patient.number
    assert body["name"][0]["family"] == "Hopper"
    assert body["name"][0]["given"][0] == "Grace"

    # The wire payload is a genuine FHIR resource: re-validate it.
    validate_resource("Patient", body)


def test_appointment_resource_validates_with_patient_reference(owner, patient_factory, scheduling_deps):
    patient = patient_factory(owner.clinic)
    deps = scheduling_deps(owner.clinic, owner.user)
    appointment = _appointment(owner.clinic, patient, deps)

    response = owner.get(f"/api/v1/fhir/Appointment/{appointment.id}/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["resourceType"] == "Appointment"
    assert body["status"] == "booked"
    identifiers = {i["system"]: i["value"] for i in body["identifier"]}
    assert identifiers[APPOINTMENT_IDENTIFIER_SYSTEM] == str(appointment.id)
    references = {p["actor"]["reference"] for p in body["participant"]}
    assert f"Patient/{patient.id}" in references
    validate_resource("Appointment", body)


def test_encounter_resource_validates_with_subject(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    encounter = _encounter(owner.clinic, patient)

    response = owner.get(f"/api/v1/fhir/Encounter/{encounter.id}/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["resourceType"] == "Encounter"
    assert body["status"] == "finished"
    assert body["subject"]["reference"] == f"Patient/{patient.id}"
    assert body["class"]["code"] == "AMB"
    validate_resource("Encounter", body)


def test_cross_clinic_fhir_read_is_404(owner, patient_factory, staff_factory):
    patient = patient_factory(owner.clinic)
    other = staff_factory(role="owner")
    response = other.get(f"/api/v1/fhir/Patient/{patient.id}/")
    assert response.status_code == 404


def test_assistant_without_analytics_view_is_forbidden(assistant, patient_factory):
    patient = patient_factory(assistant.clinic)
    response = assistant.get(f"/api/v1/fhir/Patient/{patient.id}/")
    assert response.status_code == 403


def test_invalid_resource_fails_validation():
    with pytest.raises(FhirValidationError):
        validate_resource("Patient", {"identifier": "not-a-list"})
    with pytest.raises(FhirValidationError):
        validate_resource("Bundle", {"id": "x"})


def test_renderer_rejects_malformed_projection():
    class Broken:
        pk = "00000000-0000-0000-0000-000000000000"
        clinic_id = "00000000-0000-0000-0000-000000000001"
        number = "EN-000001"
        first_name = "Ada"
        last_name = "Lovelace"
        preferred_name = ""
        date_of_birth = "not-a-date"
        sex_at_birth = "female"
        phone_e164 = ""
        email = ""
        address_json = {}
        status = "active"

    with pytest.raises(FhirValidationError):
        render_patient(Broken())
