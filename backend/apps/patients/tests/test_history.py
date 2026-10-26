"""Allergy and medical-history endpoints, summaries, audit and permissions (T106)."""

from __future__ import annotations

import pytest

from apps.core.models import AuditEvent

pytestmark = pytest.mark.django_db


def test_allergy_crud_round_trip(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Allergy", last_name="Person")
    base = f"/api/v1/patients/{patient.id}/allergies/"

    created = owner.post(
        base,
        {"substance": "Penicillin", "reaction": "Rash", "severity": "severe"},
    )
    assert created.status_code == 201, created.json()
    allergy = created.json()
    assert allergy["severity"] == "severe"
    assert allergy["is_active"] is True
    assert allergy["recorded_by"] == str(owner.user.id)
    assert allergy["recorded_at"] is not None

    listing = owner.get(base)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert listing.json()["results"][0]["substance"] == "Penicillin"

    updated = owner.patch(
        f"{base}{allergy['id']}/",
        {"severity": "mild", "is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["severity"] == "mild"
    assert updated.json()["is_active"] is False

    active_only = owner.get(base, {"active": "true"})
    assert active_only.json()["count"] == 0


def test_allergy_validation_and_unknown_id(owner, patient_factory):
    import uuid

    patient = patient_factory(owner.clinic)
    base = f"/api/v1/patients/{patient.id}/allergies/"

    blank = owner.post(base, {"substance": "   "})
    assert blank.status_code == 400
    assert "substance" in blank.json()["error"]["field_errors"]

    bad_severity = owner.post(base, {"substance": "Latex", "severity": "extreme"})
    assert bad_severity.status_code == 400
    assert "severity" in bad_severity.json()["error"]["field_errors"]

    assert owner.patch(f"{base}{uuid.uuid4()}/", {"severity": "mild"}).status_code == 404


def test_patient_detail_allergy_severity_summary(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    base = f"/api/v1/patients/{patient.id}/allergies/"
    owner.post(base, {"substance": "Latex", "severity": "mild"})
    owner.post(base, {"substance": "Penicillin", "severity": "severe"})
    owner.post(base, {"substance": "Ibuprofen", "severity": "anaphylaxis", "is_active": False})

    detail = owner.get(f"/api/v1/patients/{patient.id}/")
    assert detail.status_code == 200
    body = detail.json()

    assert body["allergy_summary"] == {
        "total": 3,
        "active_count": 2,
        "highest_severity": "severe",
        "severities": {"mild": 1, "moderate": 0, "severe": 1, "anaphylaxis": 1},
    }
    # Most severe first, inactive entries still listed in the detail payload.
    assert [row["severity"] for row in body["allergies"]] == [
        "anaphylaxis",
        "severe",
        "mild",
    ]


def test_allergy_and_history_audit_actions(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    allergy = owner.post(
        f"/api/v1/patients/{patient.id}/allergies/",
        {"substance": "Penicillin", "reaction": "Rash", "severity": "moderate"},
    ).json()
    owner.patch(
        f"/api/v1/patients/{patient.id}/allergies/{allergy['id']}/",
        {"severity": "severe"},
    )
    history = owner.post(
        f"/api/v1/patients/{patient.id}/medical-history/",
        {"kind": "condition", "label": "Type 2 diabetes", "notes": "diagnosed 2019"},
    ).json()
    owner.patch(
        f"/api/v1/patients/{patient.id}/medical-history/{history['id']}/",
        {"is_active": False},
    )

    for action in ("allergy.created", "allergy.updated", "medical_history.created", "medical_history.updated"):
        assert AuditEvent.objects.filter(clinic=owner.clinic, action=action).count() == 1

    blob = "".join(str(row.after) + str(row.before) for row in AuditEvent.objects.filter(clinic=owner.clinic))
    assert "Penicillin" not in blob
    assert "Type 2 diabetes" not in blob
    assert "diagnosed 2019" not in blob


def test_medical_history_crud_and_summary(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    base = f"/api/v1/patients/{patient.id}/medical-history/"

    condition = owner.post(
        base,
        {"kind": "condition", "label": "Asthma", "since": "2015-01-01"},
    )
    assert condition.status_code == 201, condition.json()
    assert condition.json()["kind"] == "condition"

    medication = owner.post(
        base,
        {"kind": "medication", "label": "Salbutamol", "is_active": True},
    )
    assert medication.status_code == 201

    listing = owner.get(base)
    assert listing.json()["count"] == 2
    assert [row["kind"] for row in listing.json()["results"]] == ["condition", "medication"]

    filtered = owner.get(base, {"kind": "medication"})
    assert filtered.json()["count"] == 1

    updated = owner.patch(f"{base}{condition.json()['id']}/", {"is_active": False})
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False

    inactive_only = owner.get(base, {"active": "true"})
    assert inactive_only.json()["count"] == 1

    detail = owner.get(f"/api/v1/patients/{patient.id}/")
    summary = detail.json()["medical_history_summary"]
    assert summary["total"] == 2
    assert summary["active_count"] == 1
    assert summary["by_kind"] == {"condition": 1, "surgery": 0, "medication": 1}

    invalid = owner.post(base, {"kind": "vaccination", "label": "x"})
    assert invalid.status_code == 400
    assert "kind" in invalid.json()["error"]["field_errors"]

    blank = owner.post(base, {"kind": "condition", "label": "  "})
    assert blank.status_code == 400
    assert "label" in blank.json()["error"]["field_errors"]


@pytest.mark.parametrize(
    ("role", "read", "write"),
    [
        ("owner", 200, 201),
        ("dentist", 200, 201),
        ("receptionist", 200, 201),
        ("assistant", 200, 403),
    ],
)
def test_clinical_history_permissions(staff_factory, patient_factory, role, read, write):
    staff = staff_factory(role=role)
    patient = patient_factory(staff.clinic)

    assert staff.get(f"/api/v1/patients/{patient.id}/allergies/").status_code == read
    assert staff.get(f"/api/v1/patients/{patient.id}/medical-history/").status_code == read
    assert staff.post(
        f"/api/v1/patients/{patient.id}/allergies/",
        {"substance": f"{role} substance"},
    ).status_code == write
    assert staff.post(
        f"/api/v1/patients/{patient.id}/medical-history/",
        {"kind": "condition", "label": f"{role} condition"},
    ).status_code == write


def test_clinical_records_cannot_be_added_to_an_archived_patient(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    assert owner.patch(f"/api/v1/patients/{patient.id}/", {"status": "archived"}).status_code == 200

    allergy = owner.post(f"/api/v1/patients/{patient.id}/allergies/", {"substance": "Latex"})
    assert allergy.status_code == 409
    assert allergy.json()["error"]["code"] == "patient_archived"

    history = owner.post(
        f"/api/v1/patients/{patient.id}/medical-history/",
        {"kind": "condition", "label": "Asthma"},
    )
    assert history.status_code == 409

    # Reads stay available for an archived (not deleted) record.
    assert owner.get(f"/api/v1/patients/{patient.id}/allergies/").status_code == 200


def test_deleted_patient_hides_clinical_records(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    owner.post(f"/api/v1/patients/{patient.id}/allergies/", {"substance": "Latex"})
    assert owner.delete(f"/api/v1/patients/{patient.id}/").status_code == 204

    assert owner.get(f"/api/v1/patients/{patient.id}/allergies/").status_code == 404
    assert owner.get(f"/api/v1/patients/{patient.id}/medical-history/").status_code == 404
