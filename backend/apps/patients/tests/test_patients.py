"""Patient CRUD, validation, numbering, soft delete and permissions (T106)."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections, transaction

from apps.core.models import AuditEvent
from apps.patients.models import Patient, PatientCounter
from apps.patients.services.numbering import allocate_patient_number, format_patient_number
from apps.patients.services.patients import create_patient
from apps.patients.tests.conftest import patient_payload

pytestmark = pytest.mark.django_db


def test_create_and_read_round_trip_with_all_demographic_fields(owner):
    from apps.clinics.models import Location

    room = Location.all_objects.create(clinic=owner.clinic, name="Room 1")
    response = owner.post(
        "/api/v1/patients/",
        patient_payload(
            preferred_name="Gracie",
            gender_identity="woman",
            address_json={"line1": "1 Analytics Way", "city": "Pune"},
            language="hi",
            preferred_contact="whatsapp",
            status="active",
            source="staff",
            primary_location=str(room.id),
            primary_provider=str(owner.membership.id),
            notes="Walk-in",
        ),
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["number"] == "EN-000001"
    assert body["full_name"] == "Grace Hopper"
    assert body["phone_e164"] == "+919876543210"
    assert body["email"] == "grace.hopper@example.in"
    assert body["preferred_name"] == "Gracie"
    assert body["gender_identity"] == "woman"
    assert body["address_json"] == {"line1": "1 Analytics Way", "city": "Pune"}
    assert body["language"] == "hi"
    assert body["preferred_contact"] == "whatsapp"
    assert body["primary_location"]["id"] == str(room.id)
    assert body["primary_provider"]["id"] == str(owner.membership.id)
    # T108 installs the charting app, so the summary is a live count now.
    assert body["chart_summary"] == {"available": True, "total": 0, "by_status": {}}
    assert body["duplicate_candidates"] == []

    detail = owner.get(f"/api/v1/patients/{body['id']}/")
    assert detail.status_code == 200
    assert detail.json()["number"] == "EN-000001"
    assert detail.json()["allergies"] == []
    assert detail.json()["allergy_summary"]["active_count"] == 0

    listing = owner.get("/api/v1/patients/")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["results"]] == [body["id"]]


def test_create_validates_field_errors(owner):
    missing_first = patient_payload()
    missing_first.pop("first_name")
    missing_last = patient_payload()
    missing_last.pop("last_name")

    cases = [
        (missing_first, "first_name"),
        (missing_last, "last_name"),
        (patient_payload(phone="123"), "phone"),
        (patient_payload(email="not-an-email"), "email"),
        (patient_payload(date_of_birth="2999-01-01"), "date_of_birth"),
        (patient_payload(date_of_birth="1899-12-31"), "date_of_birth"),
        (patient_payload(sex_at_birth="other"), "sex_at_birth"),
        (patient_payload(primary_location=str(uuid.uuid4())), "primary_location"),
        (patient_payload(primary_provider=str(uuid.uuid4())), "primary_provider"),
        (patient_payload(address_json=["not", "an", "object"]), "address_json"),
    ]
    for payload, field in cases:
        response = owner.post("/api/v1/patients/", payload)
        assert response.status_code == 400, (payload, response.json())
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert field in error["field_errors"], (payload, error["field_errors"])

    assert Patient.all_objects.filter(clinic=owner.clinic).count() == 0


def test_create_assigns_sequential_numbers_and_counter(owner):
    numbers = []
    for index in range(3):
        response = owner.post(
            "/api/v1/patients/",
            patient_payload(
                first_name=f"Person{index}",
                last_name=f"Numbered{index}",
                phone=f"+91987654{3210 + index}",
                email=f"numbered{index}@example.in",
            ),
        )
        assert response.status_code == 201, response.json()
        numbers.append(response.json()["number"])

    assert numbers == ["EN-000001", "EN-000002", "EN-000003"]
    counter = PatientCounter.objects.get(clinic=owner.clinic)
    assert counter.next_number == 4


def test_format_patient_number_pads_to_six_digits():
    assert format_patient_number(1) == "EN-000001"
    assert format_patient_number(123) == "EN-000123"
    assert format_patient_number(999999) == "EN-999999"
    with pytest.raises(ValueError):
        format_patient_number(0)


def test_numbers_restart_per_clinic(owner, staff_factory):
    other = staff_factory(role="owner")
    first = owner.post("/api/v1/patients/", patient_payload(first_name="ClinicA"))
    second = other.post("/api/v1/patients/", patient_payload(first_name="ClinicB", phone="+919000000001"))
    assert first.json()["number"] == "EN-000001"
    assert second.json()["number"] == "EN-000001"

    # Both clinics can hold the same number: uniqueness is per clinic.
    assert Patient.all_objects.filter(number="EN-000001").count() == 2


@pytest.mark.django_db(transaction=True)
def test_twenty_parallel_creates_are_unique_and_sequential(clinic_factory):
    clinic = clinic_factory(slug="race-clinic")

    def create_one(index: int) -> str:
        try:
            connections.close_all()
            with transaction.atomic():
                patient = create_patient(
                    clinic,
                    fields={
                        "first_name": f"Race{index}",
                        "last_name": "Parallel",
                        "sex_at_birth": "unknown",
                        "status": "active",
                        "source": "staff",
                    },
                )
            return patient.number
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=20) as pool:
        numbers = list(pool.map(create_one, range(20)))

    assert len(numbers) == 20
    assert len(set(numbers)) == 20
    assert sorted(numbers) == [format_patient_number(value) for value in range(1, 21)]
    assert Patient.all_objects.filter(clinic=clinic).count() == 20
    assert PatientCounter.objects.get(clinic=clinic).next_number == 21


def test_allocate_number_increments_the_counter(clinic_factory):
    clinic = clinic_factory(slug="counter-clinic")
    assert allocate_patient_number(clinic) == "EN-000001"
    assert allocate_patient_number(clinic) == "EN-000002"
    assert PatientCounter.objects.get(clinic=clinic).next_number == 3


def test_patch_updates_fields_and_audits(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Katha", last_name="Pollitt")
    response = owner.patch(
        f"/api/v1/patients/{patient.id}/",
        {"preferred_name": "Kate", "language": "ta", "notes": "Prefers morning slots"},
    )
    assert response.status_code == 200
    patient.refresh_from_db()
    assert patient.preferred_name == "Kate"
    assert patient.language == "ta"
    assert patient.notes == "Prefers morning slots"

    row = AuditEvent.objects.get(action="patient.updated", entity_id=str(patient.id))
    assert row.entity_type == "patients.patient"
    assert row.after["changed_fields"] == ["language", "notes", "preferred_name"]
    serialized = str(row.after) + str(row.before)
    assert "Prefers morning slots" not in serialized
    assert "Kate" not in serialized


def test_audit_snapshots_never_contain_free_text_identifiers(owner):
    response = owner.post(
        "/api/v1/patients/",
        patient_payload(first_name="Sensitive", last_name="Surname", notes="private note"),
    )
    assert response.status_code == 201
    patient_id = response.json()["id"]

    rows = AuditEvent.objects.filter(entity_id=patient_id)
    assert rows.filter(action="patient.created").count() == 1
    blob = "".join(str(row.after) + str(row.before) for row in rows)
    for leaked in ("Sensitive", "Surname", "private note", "grace.hopper@example.in"):
        assert leaked not in blob


def test_delete_soft_archives_patient_and_keeps_audit(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Zora", last_name="Neale")

    response = owner.delete(f"/api/v1/patients/{patient.id}/")
    assert response.status_code == 204

    stored = Patient.all_objects.get(pk=patient.id)
    assert stored.deleted_at is not None
    assert stored.status == Patient.Status.ARCHIVED

    assert owner.get(f"/api/v1/patients/{patient.id}/").status_code == 404
    listing = owner.get("/api/v1/patients/")
    assert listing.json()["count"] == 0

    audit = AuditEvent.objects.get(action="patient.archived", entity_id=str(patient.id))
    assert audit.after["status"] == "archived"
    assert AuditEvent.objects.filter(clinic=owner.clinic).count() >= 1


def test_deleting_twice_is_a_conflict(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    assert owner.delete(f"/api/v1/patients/{patient.id}/").status_code == 204
    again = owner.delete(f"/api/v1/patients/{patient.id}/")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "patient_archived"


def test_archived_patient_cannot_be_edited(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    archived = owner.patch(f"/api/v1/patients/{patient.id}/", {"status": "archived"})
    assert archived.status_code == 200

    denied = owner.patch(f"/api/v1/patients/{patient.id}/", {"notes": "nope"})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "patient_archived"

    # A soft-deleted patient behaves the same way for mutations.
    other = patient_factory(owner.clinic)
    assert owner.delete(f"/api/v1/patients/{other.id}/").status_code == 204
    rejected = owner.patch(f"/api/v1/patients/{other.id}/", {"notes": "nope"})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "patient_archived"


def test_merged_patients_are_hidden_from_lists(owner, patient_factory):
    target = patient_factory(owner.clinic, first_name="Target", last_name="Person")
    merged = patient_factory(
        owner.clinic,
        first_name="Merged",
        last_name="Person",
        merged_into=target,
    )

    listing = owner.get("/api/v1/patients/")
    ids = {row["id"] for row in listing.json()["results"]}
    assert str(target.id) in ids
    assert str(merged.id) not in ids
    # ...but the row is still reachable by id for merge bookkeeping.
    assert owner.get(f"/api/v1/patients/{merged.id}/").status_code == 200


def test_foreign_patient_is_404(owner, staff_factory, patient_factory):
    intruder = staff_factory(role="owner")
    victim = patient_factory(owner.clinic, first_name="Private", last_name="Person")

    assert intruder.get(f"/api/v1/patients/{victim.id}/").status_code == 404
    assert intruder.patch(f"/api/v1/patients/{victim.id}/", {"notes": "x"}).status_code == 404
    assert intruder.delete(f"/api/v1/patients/{victim.id}/").status_code == 404
    assert intruder.post(
        f"/api/v1/patients/{victim.id}/allergies/", {"substance": "Penicillin"}
    ).status_code == 404
    assert intruder.get(f"/api/v1/patients/{victim.id}/timeline/").status_code == 404
    assert intruder.get(f"/api/v1/patients/{victim.id}/export/").status_code == 404

    # The foreign list is empty rather than erroring, and never leaks the row.
    listing = intruder.get("/api/v1/patients/")
    assert listing.status_code == 200
    assert listing.json()["count"] == 0


@pytest.mark.parametrize(
    ("role", "create", "patch", "delete"),
    [
        ("owner", 201, 200, 204),
        ("dentist", 201, 200, 204),
        ("receptionist", 201, 200, 403),
        ("assistant", 403, 403, 403),
    ],
)
def test_role_permissions_on_patient_crud(staff_factory, patient_factory, role, create, patch, delete):
    staff = staff_factory(role=role)
    patient = patient_factory(staff.clinic, first_name="Role", last_name=role.title())

    assert staff.get("/api/v1/patients/").status_code == 200
    assert staff.get(f"/api/v1/patients/{patient.id}/").status_code == 200

    created = staff.post(
        "/api/v1/patients/",
        patient_payload(first_name=f"New{role}", last_name="Person"),
    )
    assert created.status_code == create, created.json()
    assert staff.patch(
        f"/api/v1/patients/{patient.id}/", {"preferred_name": role}
    ).status_code == patch
    assert staff.delete(f"/api/v1/patients/{patient.id}/").status_code == delete


def test_unauthenticated_requests_are_401(api_client):
    response = api_client.get("/api/v1/patients/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_list_is_paginated_and_ordered_by_last_name(owner, patient_factory):
    for last in ("Zephyr", "Adams", "Brin"):
        patient_factory(owner.clinic, first_name="X", last_name=last)

    response = owner.get("/api/v1/patients/", {"page_size": 2})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"count", "next", "previous", "results"}
    assert body["count"] == 3
    assert [row["last_name"] for row in body["results"]] == ["Adams", "Brin"]
    assert body["next"] is not None

    page_two = owner.get("/api/v1/patients/", {"page_size": 2, "page": 2})
    assert [row["last_name"] for row in page_two.json()["results"]] == ["Zephyr"]
