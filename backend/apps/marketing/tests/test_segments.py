"""Segment predicate allowlist and preview (T132)."""

from __future__ import annotations

import pytest

from apps.marketing.services import segments
from apps.marketing.services.guardrails import ALLOWED_FIELDS, PredicateError

pytestmark = pytest.mark.django_db

CLINICAL_KEYS = {
    "diagnosis",
    "diagnosis_codes",
    "notes",
    "clinical_notes",
    "allergies",
    "medical_history",
    "tooth",
    "procedure_code",
}


def _create_segment(client, name, predicate):
    return client.post(
        "/api/v1/segments/",
        {"name": name, "predicate_json": predicate},
    )


def test_unsupported_field_is_rejected_at_save(owner):
    response = _create_segment(
        owner,
        "Bad",
        {"all": [{"field": "birthday", "op": "eq", "value": "x"}]},
    )
    assert response.status_code == 400, response.content
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "predicate_json" in error["field_errors"]


def test_clinical_field_is_rejected_at_save(owner):
    response = _create_segment(
        owner,
        "Clinical",
        {"all": [{"field": "diagnosis_codes", "op": "eq", "value": "K02.9"}]},
    )
    assert response.status_code == 400, response.content
    message = " ".join(response.json()["error"]["field_errors"]["predicate_json"])
    assert "clinical" in message.lower()


def test_unknown_operator_is_rejected(owner):
    response = _create_segment(
        owner,
        "Op",
        {"all": [{"field": "status", "op": "regex", "value": "active"}]},
    )
    assert response.status_code == 400
    assert "predicate_json" in response.json()["error"]["field_errors"]


def test_valid_segment_is_created(owner):
    response = _create_segment(
        owner,
        "Lapsed 180",
        {"all": [{"field": "last_visit_days", "op": "gte", "value": 180}]},
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["name"] == "Lapsed 180"
    assert body["predicate_json"] == {
        "all": [{"field": "last_visit_days", "op": "gte", "value": 180}]
    }


def test_preview_returns_count_and_non_clinical_sample(owner, patient_factory, visit_factory):
    p_lapsed = patient_factory(owner.clinic, first_name="Lapsed")
    visit_factory(p_lapsed, days_ago=200)
    p_recent = patient_factory(owner.clinic, first_name="Recent")
    visit_factory(p_recent, days_ago=10)
    patient_factory(owner.clinic, first_name="Never")

    response = owner.post(
        "/api/v1/segments/preview/",
        {"predicate_json": {"all": [{"field": "last_visit_days", "op": "gte", "value": 180}]}},
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["count"] == 1
    assert len(body["sample"]) == 1
    sample = body["sample"][0]
    assert sample["patient_id"] == str(p_lapsed.id)
    # The sample carries only allowlisted fields: no clinical data anywhere.
    assert set(sample["fields"]) == set(ALLOWED_FIELDS)
    assert not (CLINICAL_KEYS & set(sample["fields"]))
    assert body["fields"] == list(ALLOWED_FIELDS)


def test_preview_supports_any_and_in(owner, patient_factory):
    patient_factory(owner.clinic, first_name="A", status="active")
    patient_factory(owner.clinic, first_name="B", status="archived")
    response = owner.post(
        "/api/v1/segments/preview/",
        {
            "predicate_json": {
                "any": [{"field": "status", "op": "in", "value": ["archived"]}]
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_segment_preview_is_clinic_scoped(owner, patient_factory, staff_factory):
    other = staff_factory(role="owner")
    created = _create_segment(
        other,
        "Other clinic",
        {"all": [{"field": "status", "op": "eq", "value": "active"}]},
    )
    assert created.status_code == 201
    segment_id = created.json()["id"]

    response = owner.get(f"/api/v1/segments/{segment_id}/preview/")
    assert response.status_code == 404, response.content


def test_validate_predicate_rejects_malformed():
    with pytest.raises(PredicateError):
        segments.validate_predicate({"all": []})
    with pytest.raises(PredicateError):
        segments.validate_predicate({"all": [{"field": "status"}]})
    with pytest.raises(PredicateError):
        segments.validate_predicate({"all": [{"field": "status", "op": "gte", "value": "x"}]})
