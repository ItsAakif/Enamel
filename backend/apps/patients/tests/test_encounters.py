"""Encounter endpoints: appointment linking, audit and permissions (T106)."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db


def _base(patient) -> str:
    return f"/api/v1/patients/{patient.id}/encounters/"


def test_create_encounter_without_appointment(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    response = owner.post(
        _base(patient),
        {
            "kind": "treatment",
            "chief_complaint": "Sensitivity on the upper left",
            "diagnosis_codes": ["K02.9"],
            "notes": "Composite filling",
        },
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["kind"] == "treatment"
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert body["appointment_id"] is None
    assert body["diagnosis_codes"] == ["K02.9"]

    audit = AuditEvent.objects.get(action="encounter.created", entity_id=body["id"])
    assert audit.after["kind"] == "treatment"
    assert audit.after["code_count"] == 1
    assert "Sensitivity" not in str(audit.after)
    assert "Composite filling" not in str(audit.after)


def test_encounter_accepts_appointment_uuid_when_scheduling_is_absent(
    owner, patient_factory, fake_scheduling
):
    """Without the scheduling app the id is an opaque reference (documented).

    T110 ships the scheduling app, so the "absent" state is now simulated
    through the app registry instead of being the real state of the project.
    """

    fake_scheduling(exists=False, installed=False)
    patient = patient_factory(owner.clinic)
    appointment_id = uuid.uuid4()
    response = owner.post(
        _base(patient),
        {"kind": "follow_up", "appointment_id": str(appointment_id)},
    )
    assert response.status_code == 201
    assert response.json()["appointment_id"] == str(appointment_id)


def test_encounter_rejects_an_unknown_appointment_when_scheduling_exists(
    owner, patient_factory, fake_scheduling
):
    fake_scheduling(exists=False)
    patient = patient_factory(owner.clinic)

    response = owner.post(
        _base(patient),
        {"kind": "exam", "appointment_id": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert "appointment_id" in response.json()["error"]["field_errors"]


def test_encounter_links_a_known_appointment_when_scheduling_exists(
    owner, patient_factory, fake_scheduling
):
    fake_scheduling(exists=True)
    patient = patient_factory(owner.clinic)
    appointment_id = uuid.uuid4()

    response = owner.post(
        _base(patient),
        {"kind": "exam", "appointment_id": str(appointment_id)},
    )
    assert response.status_code == 201
    assert response.json()["appointment_id"] == str(appointment_id)


def test_provider_must_belong_to_the_clinic(owner, staff_factory, patient_factory):
    other = staff_factory(role="owner")
    patient = patient_factory(owner.clinic)

    foreign = owner.post(_base(patient), {"provider": str(other.membership.id)})
    assert foreign.status_code == 400
    assert "provider" in foreign.json()["error"]["field_errors"]

    accepted = owner.post(_base(patient), {"provider": str(owner.membership.id)})
    assert accepted.status_code == 201
    assert accepted.json()["provider"] == str(owner.membership.id)


def test_encounter_list_detail_and_filters(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    older = owner.post(
        _base(patient),
        {"kind": "exam", "occurred_at": "2026-01-01T09:00:00Z"},
    ).json()
    newer = owner.post(
        _base(patient),
        {"kind": "telehealth", "occurred_at": "2026-06-01T09:00:00Z", "status": "finalized"},
    ).json()

    listing = owner.get(_base(patient))
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [newer["id"], older["id"]]

    assert owner.get(_base(patient), {"kind": "exam"}).json()["count"] == 1
    assert owner.get(_base(patient), {"status": "finalized"}).json()["count"] == 1

    detail = owner.get(f"{_base(patient)}{older['id']}/")
    assert detail.status_code == 200
    assert detail.json()["id"] == older["id"]

    missing = owner.get(f"{_base(patient)}{uuid.uuid4()}/")
    assert missing.status_code == 404


def test_encounter_permissions_per_role(staff_factory, patient_factory):
    expectations = {
        "owner": (200, 201),
        "dentist": (200, 201),
        # The permission registry has no ``encounters.view``; the read gate is
        # ``clinical.history.view``, which the receptionist holds. Writes still
        # require ``encounters.edit``.
        "receptionist": (200, 403),
        "assistant": (200, 403),
    }
    for role, (read_code, write_code) in expectations.items():
        staff = staff_factory(role=role)
        patient = patient_factory(staff.clinic)
        assert staff.get(_base(patient)).status_code == read_code, role
        written = staff.post(_base(patient), {"kind": "exam"})
        assert written.status_code == write_code, (role, written.json())


def test_encounter_on_archived_patient_is_409(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    assert owner.patch(f"/api/v1/patients/{patient.id}/", {"status": "archived"}).status_code == 200

    response = owner.post(_base(patient), {"kind": "exam"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "patient_archived"

    assert owner.get(_base(patient)).status_code == 200


def test_encounter_validation(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    bad_kind = owner.post(_base(patient), {"kind": "surgery"})
    assert bad_kind.status_code == 400
    assert "kind" in bad_kind.json()["error"]["field_errors"]

    bad_status = owner.post(_base(patient), {"status": "signed"})
    assert bad_status.status_code == 400
    assert "status" in bad_status.json()["error"]["field_errors"]

    too_many = owner.post(
        _base(patient),
        {"diagnosis_codes": [f"K{i:02d}" for i in range(60)]},
    )
    assert too_many.status_code == 400
    assert "diagnosis_codes" in too_many.json()["error"]["field_errors"]

    future = owner.post(_base(patient), {"occurred_at": "2099-01-01T00:00:00Z"})
    # Future timestamps are allowed (a scheduled procedure note) but must parse.
    assert future.status_code == 201
    assert datetime.fromisoformat(future.json()["occurred_at"]) is not None
