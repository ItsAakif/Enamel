"""Sync idempotency, retry, partial-write and conflict handling (T130)."""

from __future__ import annotations

import pytest

from apps.interop.models import (
    ConflictStatus,
    EntityType,
    ExternalIdMap,
    SyncConflict,
    SyncRun,
)
from apps.interop.services import ids as id_service
from apps.patients.models import Patient
from apps.scheduling.models import Appointment

pytestmark = pytest.mark.django_db


def _patient_seed(external_id: str = "E1") -> dict:
    return {
        "external_id": external_id,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "date_of_birth": "1990-01-01",
    }


def _appointment_seed(external_id: str = "A1", patient_external_id: str = "E1") -> dict:
    return {
        "external_id": external_id,
        "patient_external_id": patient_external_id,
        "start_at": "2030-01-02T10:00:00+00:00",
        "end_at": "2030-01-02T10:30:00+00:00",
        "status": "scheduled",
    }


def test_second_run_creates_no_duplicates(owner, connector_factory, scheduling_deps):
    scheduling_deps(owner.clinic, owner.user)
    connector = connector_factory(
        owner.clinic,
        settings_json={
            "patients": [_patient_seed()],
            "appointments": [_appointment_seed()],
        },
    )

    first = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert first.status_code == 200, first.content
    assert first.json()["run"]["status"] == "succeeded"

    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 1
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 1
    assert ExternalIdMap.all_objects.filter(connector=connector).count() == 2

    second = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert second.status_code == 200, second.content
    counts = second.json()["run"]["counts_json"]
    assert counts["patients_created"] == 0
    assert counts["patients_skipped"] == 1
    assert counts["appointments_created"] == 0
    assert counts["appointments_skipped"] == 1

    # Zero duplicates on the second run.
    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 1
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 1
    assert ExternalIdMap.all_objects.filter(connector=connector).count() == 2


def test_injected_500_is_retried_then_succeeds(owner, connector_factory):
    connector = connector_factory(
        owner.clinic,
        settings_json={
            "patients": [_patient_seed()],
            "faults": {"pull_patients": {"error_500": 1}},
        },
    )
    response = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert response.status_code == 200, response.content
    run = response.json()["run"]
    assert run["status"] == "succeeded"
    counts = run["counts_json"]
    assert counts["retries"] == 1
    assert len(counts["errors"]) == 1
    assert counts["errors"][0]["code"] == "connector_server_error"
    assert counts["patients_created"] == 1
    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 1


def test_partial_write_is_reported_partial_with_references(owner, connector_factory):
    connector = connector_factory(
        owner.clinic,
        settings_json={
            "patients": [_patient_seed()],
            "appointments": [_appointment_seed()],
            "faults": {"partial_write": True},
        },
    )
    response = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert response.status_code == 200, response.content
    run = response.json()["run"]
    assert run["status"] == "partial"
    assert run["error_code"] == "connector_partial_write"
    counts = run["counts_json"]
    assert counts["patients_created"] == 1
    assert len(counts["references"]) == 1
    # The patient exists; the appointment does not.
    patient = Patient.all_objects.get(clinic=owner.clinic)
    assert counts["references"] == [str(patient.id)]
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 0


def test_conflict_is_queued_not_overwritten(owner, connector_factory, patient_factory):
    connector = connector_factory(
        owner.clinic,
        settings_json={"patients": [_patient_seed()]},
    )
    first = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert first.json()["run"]["status"] == "succeeded"

    mapped = id_service.get_mapping(connector, EntityType.PATIENT, "E1")
    assert mapped is not None
    mapped_internal = mapped.internal_id

    # A different internal patient that the next payload claims to be.
    other = patient_factory(owner.clinic, first_name="Other", last_name="Person")

    connector.settings_json = {
        "patients": [{**_patient_seed(), "internal_id": str(other.id)}]
    }
    connector.save(update_fields=["settings_json", "updated_at"])

    second = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    assert second.status_code == 200, second.content
    counts = second.json()["run"]["counts_json"]
    assert counts["patients_conflicted"] == 1

    conflict = SyncConflict.all_objects.get(connector=connector, external_id="E1")
    assert conflict.status == ConflictStatus.OPEN
    assert str(conflict.internal_id) == str(other.id)

    # The mapping and both patients are untouched.
    refreshed = id_service.get_mapping(connector, EntityType.PATIENT, "E1")
    assert refreshed.internal_id == mapped_internal
    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 2


def test_runs_endpoint_lists_runs_and_is_clinic_scoped(owner, staff_factory, connector_factory):
    connector = connector_factory(owner.clinic, settings_json={"patients": [_patient_seed()]})
    owner.post(f"/api/v1/connectors/{connector.id}/sync/")

    listing = owner.get(f"/api/v1/connectors/{connector.id}/runs/")
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert SyncRun.all_objects.filter(connector=connector).count() == 1

    other = staff_factory(role="owner")
    assert other.get(f"/api/v1/connectors/{connector.id}/runs/").status_code == 404
    assert other.post(f"/api/v1/connectors/{connector.id}/sync/").status_code == 404


def test_run_is_resumable_from_cursor(owner, connector_factory):
    connector = connector_factory(
        owner.clinic,
        settings_json={
            "patients": [
                {**_patient_seed("E1"), "updated_at": "2026-01-01T00:00:00+00:00"},
                {**_patient_seed("E2"), "updated_at": "2026-02-01T00:00:00+00:00"},
            ],
            "faults": {"partial_write": True},
        },
    )
    first = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    run = first.json()["run"]
    assert run["status"] == "partial"
    assert run["cursor"] == "2026-02-01T00:00:00+00:00"

    # A second run resumes from the cursor; both patients are already mapped.
    second = owner.post(f"/api/v1/connectors/{connector.id}/sync/")
    counts = second.json()["run"]["counts_json"]
    assert counts["since"] == "2026-02-01T00:00:00+00:00"
    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 2
    assert SyncRun.all_objects.filter(connector=connector).count() == 2
