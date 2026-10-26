"""Duplicate detection, override audit and phone normalization (T106)."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.patients.models import Patient
from apps.patients.services.dedup import (
    InvalidPhoneNumber,
    find_duplicates,
    normalize_phone,
)
from apps.patients.tests.conftest import patient_payload

pytestmark = pytest.mark.django_db


def test_normalize_phone_uses_india_region_by_default():
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""


def test_normalize_phone_rejects_junk():
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("123")
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone("not a phone")


def test_find_duplicates_reports_every_reason(owner, patient_factory):
    existing = patient_factory(
        owner.clinic,
        first_name="Aarav",
        last_name="Sharma",
        date_of_birth="1985-06-01",
        phone_e164="+919876543210",
        email="aarav@example.in",
    )

    candidates = find_duplicates(
        owner.clinic,
        first_name="Arav",
        last_name="sharma",
        date_of_birth="1985-06-01",
        phone_e164="+919876543210",
        email="AARAV@example.in",
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.id == str(existing.id)
    assert candidate.number == existing.number
    assert candidate.name == "Aarav Sharma"
    assert set(candidate.reasons) == {"phone", "email", "name_and_dob"}


def test_find_duplicates_is_clinic_scoped(owner, staff_factory, patient_factory):
    other = staff_factory(role="owner")
    patient_factory(
        other.clinic,
        first_name="Aarav",
        last_name="Sharma",
        phone_e164="+919876543210",
        email="aarav@example.in",
    )

    assert (
        find_duplicates(
            owner.clinic,
            last_name="Sharma",
            phone_e164="+919876543210",
            email="aarav@example.in",
        )
        == []
    )


def test_deleted_and_merged_patients_are_not_candidates(owner, patient_factory):
    target = patient_factory(owner.clinic, first_name="Keep", last_name="Target")
    deleted = patient_factory(
        owner.clinic,
        first_name="Gone",
        last_name="Person",
        phone_e164="+919000000001",
        deleted_at=timezone.now(),
        status="archived",
    )
    merged = patient_factory(
        owner.clinic,
        first_name="Merged",
        last_name="Person",
        phone_e164="+919000000002",
        merged_into=target,
    )

    assert find_duplicates(owner.clinic, phone_e164="+919000000001") == []
    assert find_duplicates(owner.clinic, phone_e164="+919000000002") == []
    assert deleted.deleted_at is not None
    assert merged.merged_into_id == target.id


def test_create_returns_409_with_candidates(owner, patient_factory):
    existing = patient_factory(
        owner.clinic,
        first_name="Meera",
        last_name="Iyer",
        date_of_birth="1979-02-02",
        phone_e164="+919876500002",
        email="meera@example.in",
    )

    response = owner.post(
        "/api/v1/patients/",
        patient_payload(
            first_name="Meera",
            last_name="Iyer",
            date_of_birth="1979-02-02",
            phone="+91 98765 00002",
            email="MEERA@example.in",
        ),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "duplicate_candidates"
    candidates = error["details"]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["id"] == str(existing.id)
    assert candidates[0]["number"] == existing.number
    assert candidates[0]["name"] == "Meera Iyer"
    assert set(candidates[0]["reasons"]) == {"phone", "email", "name_and_dob"}
    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 1


def test_override_creates_and_audits_the_decision(owner, patient_factory):
    patient_factory(
        owner.clinic,
        first_name="Meera",
        last_name="Iyer",
        phone_e164="+919876500002",
        email="meera@example.in",
    )

    response = owner.post(
        "/api/v1/patients/",
        patient_payload(
            first_name="Meera",
            last_name="Iyer",
            phone="+91 98765 00002",
            email="meera@example.in",
            override_duplicates=True,
        ),
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["number"] == "EN-000002"
    assert [candidate["reasons"] for candidate in body["duplicate_candidates"]] == [["phone", "email"]]

    audit = AuditEvent.objects.get(
        action="patient.duplicate_overridden",
        entity_id=body["id"],
    )
    assert audit.after["override"] is True
    assert audit.after["candidate_ids"]
    assert AuditEvent.objects.filter(clinic=owner.clinic, action="patient.created").count() == 1


def test_create_without_matches_still_reports_an_empty_candidate_list(owner):
    response = owner.post("/api/v1/patients/", patient_payload(first_name="Unique", last_name="Person"))
    assert response.status_code == 201
    assert response.json()["duplicate_candidates"] == []


def test_duplicate_check_ignores_deleted_records(owner, patient_factory):
    patient_factory(
        owner.clinic,
        first_name="Ghost",
        last_name="Person",
        phone_e164="+919111111111",
        deleted_at=timezone.now(),
        status="archived",
    )

    response = owner.post(
        "/api/v1/patients/",
        patient_payload(first_name="Ghost", last_name="Person", phone="+91 91111 11111"),
    )
    assert response.status_code == 201
