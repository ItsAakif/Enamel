"""Atomic batch save semantics (T108)."""

from __future__ import annotations

import pytest

from apps.charting.models import ChartBatchLog, ConditionCode, ToothState

pytestmark = pytest.mark.django_db


def _state(patient, tooth, code="caries"):
    return ToothState.all_objects.filter(
        patient=patient, tooth=tooth, condition_id=code
    ).first()


def test_one_invalid_item_writes_zero_rows(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [
            {"tooth": 11, "condition_code": "caries", "surfaces": ["M"]},
            {"tooth": 12, "condition_code": "not_a_condition", "surfaces": ["M"]},
        ],
        key="atomic-1",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "changes.1.condition_code" in error["field_errors"]
    # Nothing at all was written: not the valid first item, not the invalid one.
    assert ToothState.all_objects.filter(patient=patient).count() == 0
    assert ChartBatchLog.all_objects.filter(patient=patient).count() == 0


def test_invalid_surface_on_the_second_item_writes_zero_rows(
    owner, patient_factory, save_batch
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [
            {"tooth": 11, "condition_code": "caries", "surfaces": ["M"]},
            {"tooth": 16, "condition_code": "caries", "surfaces": ["I"]},
        ],
        key="atomic-2",
    )
    assert response.status_code == 400
    assert "changes.1.surfaces" in response.json()["error"]["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 0


def test_unknown_tooth_code_is_a_field_error(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [{"tooth": 19, "condition_code": "caries"}],
        key="bad-tooth",
    )
    assert response.status_code == 400
    assert "changes.0.tooth" in response.json()["error"]["field_errors"]
    assert ToothState.all_objects.filter(patient=patient).count() == 0


def test_empty_changes_and_missing_key_are_rejected(owner, patient_factory):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    url = f"/api/v1/patients/{patient.id}/chart/batch/"

    empty = owner.post(url, {"changes": [], "idempotency_key": "empty-1"})
    assert empty.status_code == 400
    assert "changes" in empty.json()["error"]["field_errors"]

    no_key = owner.post(url, {"changes": [{"tooth": 11, "condition_code": "caries"}]})
    assert no_key.status_code == 400
    assert "idempotency_key" in no_key.json()["error"]["field_errors"]


def test_re_adding_an_active_condition_is_idempotent(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    first = save_batch(
        owner,
        patient,
        [{"tooth": 15, "condition_code": "caries", "surfaces": ["O"]}],
        key="idem-1",
    )
    assert first.status_code == 200
    state_id = first.json()["applied"][0]["id"]

    again = save_batch(
        owner,
        patient,
        [{"tooth": 15, "condition_code": "caries", "surfaces": ["O", "D"]}],
        key="idem-2",
    )
    assert again.status_code == 200, again.json()
    applied = again.json()["applied"][0]
    assert applied["action"] == "unchanged"
    assert applied["id"] == state_id
    assert applied["version"] == 1

    assert ToothState.all_objects.filter(patient=patient, tooth=15).count() == 1
    # The existing row was not modified by the idempotent re-add.
    assert _state(patient, 15).surfaces == ["O"]


def test_force_updates_an_existing_state_without_a_version(
    owner, patient_factory, save_batch
):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [{"tooth": 15, "condition_code": "caries", "surfaces": ["O"]}],
        key="force-1",
    )
    forced = save_batch(
        owner,
        patient,
        [{"tooth": 15, "condition_code": "caries", "surfaces": ["O", "D"], "severity": 3}],
        key="force-2",
        force=True,
    )
    assert forced.status_code == 200, forced.json()
    applied = forced.json()["applied"][0]
    assert applied["action"] == "updated"
    assert applied["version"] == 2
    state = _state(patient, 15)
    assert state.surfaces == ["O", "D"]
    assert state.severity == 3


def test_same_idempotency_key_replays_the_stored_result(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    body_changes = [{"tooth": 17, "condition_code": "caries", "surfaces": ["O"]}]

    first = save_batch(owner, patient, body_changes, key="replay-1")
    assert first.status_code == 200
    assert first.json()["replayed"] is False

    second = save_batch(owner, patient, body_changes, key="replay-1")
    assert second.status_code == 200
    payload = second.json()
    assert payload["replayed"] is True
    assert payload["applied"] == first.json()["applied"]
    assert payload["states"] == first.json()["states"]

    assert ToothState.all_objects.filter(patient=patient).count() == 1
    assert ChartBatchLog.all_objects.filter(patient=patient, key="replay-1").count() == 1


def test_status_defaults_from_the_condition(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    response = save_batch(
        owner,
        patient,
        [
            {"tooth": 18, "condition_code": "extraction_planned"},
            {"tooth": 28, "condition_code": "caries", "surfaces": ["O"], "status": "planned"},
        ],
        key="default-status-1",
    )
    assert response.status_code == 200, response.json()
    assert _state(patient, 18, "extraction_planned").status == "planned"
    assert _state(patient, 28).status == "planned"
    assert ConditionCode.objects.get(code="extraction_planned").default_status == "planned"


def test_archive_and_re_add_in_one_batch(owner, patient_factory, save_batch):
    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [{"tooth": 25, "condition_code": "caries", "surfaces": ["M"]}],
        key="inbatch-1",
    )
    response = save_batch(
        owner,
        patient,
        [
            {"tooth": 25, "condition_code": "caries", "archive": True},
            {"tooth": 25, "condition_code": "crown"},
        ],
        key="inbatch-2",
    )
    assert response.status_code == 200, response.json()
    actions = {item["condition_code"]: item["action"] for item in response.json()["applied"]}
    assert actions == {"caries": "archived", "crown": "created"}
    active = ToothState.all_objects.filter(patient=patient, archived_at__isnull=True)
    assert {row.condition_id for row in active} == {"crown"}


def test_batch_isolated_to_the_addressed_patient(owner, patient_factory, save_batch):
    patient_a = patient_factory(owner.clinic, first_name="Ann", date_of_birth="1990-01-01")
    patient_b = patient_factory(owner.clinic, first_name="Ben", date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient_a,
        [{"tooth": 11, "condition_code": "caries", "surfaces": ["M"]}],
        key="patient-a",
    )
    assert ToothState.all_objects.filter(patient=patient_b).count() == 0
    assert ToothState.all_objects.filter(patient=patient_a).count() == 1


def test_batch_writes_an_audit_row_per_change(owner, patient_factory, save_batch):
    from apps.core.models import AuditEvent

    patient = patient_factory(owner.clinic, date_of_birth="1990-01-01")
    save_batch(
        owner,
        patient,
        [
            {"tooth": 11, "condition_code": "caries", "surfaces": ["M"], "note": "secret text"},
            {"tooth": 12, "condition_code": "crown"},
        ],
        key="audit-1",
    )
    rows = AuditEvent.objects.filter(
        action="chart.created", entity_type="charting.toothstate"
    ).order_by("id")
    assert rows.count() == 2
    serialized = "".join(str(row.after) + str(row.before) for row in rows)
    # The clinical note never reaches the audit log.
    assert "secret text" not in serialized
