"""Timeline assembly/pagination and JSON/PDF export (T106)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.models import AuditEvent
from apps.patients.models import Allergy, Encounter, MedicalHistory, Patient
from apps.patients.services.timeline import (
    TimelineEvent,
    build_timeline,
    decode_cursor,
    paginate_timeline,
)

pytestmark = pytest.mark.django_db


def test_timeline_orders_newest_first_without_duplicates(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Timeline", last_name="Person")
    base = f"/api/v1/patients/{patient.id}/"

    owner.post(
        f"{base}encounters/",
        {"kind": "exam", "occurred_at": "2026-03-01T09:00:00Z"},
    )
    owner.post(f"{base}allergies/", {"substance": "Penicillin", "severity": "severe"})
    owner.post(f"{base}medical-history/", {"kind": "condition", "label": "Asthma"})

    response = owner.get(f"{base}timeline/")
    assert response.status_code == 200
    body = response.json()
    assert body["previous"] is None
    results = body["results"]

    timestamps = [datetime.fromisoformat(row["at"]) for row in results]
    assert timestamps == sorted(timestamps, reverse=True)

    types = [row["type"] for row in results]
    assert types.count("encounter.created") == 1
    assert types.count("allergy.created") == 1
    assert types.count("medical_history.created") == 1
    assert types.count("patient.created") == 1

    keys = [(row["type"], row["entity_id"]) for row in results]
    assert len(keys) == len(set(keys))

    # The allergy event carries only the enum values the allergies endpoint
    # already returns, never the substance or the reaction text.
    allergy_event = next(row for row in results if row["type"] == "allergy.created")
    assert allergy_event["metadata"] == {"severity": "severe", "is_active": True}
    assert set(allergy_event) == {"type", "at", "title", "summary", "entity_id", "metadata"}
    assert "Penicillin" not in response.content.decode()


def test_timeline_reports_a_later_edit_once(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Edited", last_name="Person")
    old = timezone.now() - timedelta(days=30)
    Patient.all_objects.filter(pk=patient.pk).update(created_at=old, updated_at=old)

    owner.patch(f"/api/v1/patients/{patient.id}/", {"preferred_name": "Edit"})

    response = owner.get(f"/api/v1/patients/{patient.id}/timeline/")
    types = [row["type"] for row in response.json()["results"]]
    assert types.count("patient.created") == 1
    assert types.count("patient.updated") == 1


def test_timeline_query_count_does_not_grow_with_events(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    clinic = owner.clinic
    base_time = timezone.now() - timedelta(days=400)
    for index in range(25):
        Encounter.all_objects.create(
            clinic=clinic,
            patient=patient,
            occurred_at=base_time + timedelta(days=index),
        )
        Allergy.all_objects.create(
            clinic=clinic,
            patient=patient,
            substance=f"Substance {index}",
            recorded_at=base_time + timedelta(days=index, hours=1),
        )
        MedicalHistory.all_objects.create(
            clinic=clinic, patient=patient, kind="condition", label=f"Label {index}"
        )

    with CaptureQueriesContext(connection) as few:
        small = build_timeline(patient)
    assert len(small) >= 76

    snapshot = Patient.all_objects.select_related("clinic").get(pk=patient.pk)
    with CaptureQueriesContext(connection) as many:
        events = build_timeline(snapshot)

    assert len(events) == len(small)
    # ``with_clinic`` issues constant SET/RESTORE statements per source; they
    # do not scale with the event count. The equality below is the real
    # no-N+1 assertion; the ceiling only catches an accidental per-event query.
    assert len(many.captured_queries) == len(few.captured_queries)
    assert len(many.captured_queries) <= 12


def test_timeline_cursor_pagination_walks_without_overlap(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    for index in range(5):
        owner.post(
            f"/api/v1/patients/{patient.id}/encounters/",
            {"kind": "exam", "occurred_at": f"2026-02-{index + 1:02d}T09:00:00Z"},
        )

    first = owner.get(f"/api/v1/patients/{patient.id}/timeline/", {"page_size": 3})
    body = first.json()
    assert len(body["results"]) == 3
    assert body["next"] is not None

    second = owner.get(body["next"])
    second_body = second.json()
    assert second_body["results"]
    first_ids = {(row["type"], row["entity_id"]) for row in body["results"]}
    second_ids = {(row["type"], row["entity_id"]) for row in second_body["results"]}
    assert first_ids.isdisjoint(second_ids)

    cursor = body["next"].split("cursor=")[1].split("&")[0]
    at, entity_id = decode_cursor(cursor)
    assert isinstance(at, datetime)
    assert entity_id == body["results"][-1]["entity_id"]


def test_timeline_rejects_an_invalid_cursor_and_page_size(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    url = f"/api/v1/patients/{patient.id}/timeline/"

    bad_cursor = owner.get(url, {"cursor": "not-a-cursor"})
    assert bad_cursor.status_code == 400
    assert "cursor" in bad_cursor.json()["error"]["field_errors"]

    bad_size = owner.get(url, {"page_size": 500})
    assert bad_size.status_code == 400
    assert "page_size" in bad_size.json()["error"]["field_errors"]


def test_paginate_timeline_uses_a_positional_cursor():
    now = timezone.now()
    events = [
        TimelineEvent("a", now, "a", "a", "00000000-0000-0000-0000-00000000000a"),
        TimelineEvent(
            "b",
            now - timedelta(hours=1),
            "b",
            "b",
            "00000000-0000-0000-0000-00000000000b",
        ),
    ]
    page, next_cursor = paginate_timeline(events, page_size=1)
    assert [event.type for event in page] == ["a"]
    assert next_cursor
    assert isinstance(next_cursor, str)

    rest, final = paginate_timeline(events, cursor=next_cursor, page_size=1)
    assert [event.type for event in rest] == ["b"]
    assert final is None


def test_export_json_contains_only_this_patient_and_is_audited(
    owner, staff_factory, patient_factory
):
    other = staff_factory(role="owner")
    patient = patient_factory(
        owner.clinic,
        first_name="Export",
        last_name="Person",
        phone_e164="+919876543210",
        email="export@example.in",
    )
    patient_factory(other.clinic, first_name="Foreign", last_name="Person")

    owner.post(
        f"/api/v1/patients/{patient.id}/allergies/",
        {"substance": "Penicillin", "severity": "severe"},
    )
    owner.post(
        f"/api/v1/patients/{patient.id}/medical-history/",
        {"kind": "condition", "label": "Asthma"},
    )
    owner.post(f"/api/v1/patients/{patient.id}/encounters/", {"kind": "exam"})

    response = owner.get(f"/api/v1/patients/{patient.id}/export/", {"format": "json"})
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()

    assert body["clinic"]["id"] == str(owner.clinic.id)
    assert body["patient"]["id"] == str(patient.id)
    assert body["patient"]["full_name"] == "Export Person"
    assert body["export"]["format"] == "json"
    assert body["export"]["rows"] == 3
    serialized = response.content.decode()
    assert "Foreign" not in serialized
    assert str(other.clinic.id) not in serialized

    audit = AuditEvent.objects.get(action="patient.exported", entity_id=str(patient.id))
    assert audit.after["format"] == "json"
    assert audit.after["rows"] == 3


def test_export_pdf_returns_a_labelled_pdf_and_is_audited(owner, patient_factory):
    patient = patient_factory(owner.clinic, first_name="Pdf", last_name="Person")
    owner.post(
        f"/api/v1/patients/{patient.id}/allergies/",
        {"substance": "Penicillin", "severity": "mild"},
    )

    response = owner.get(f"/api/v1/patients/{patient.id}/export/", {"format": "pdf"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == f'attachment; filename="{patient.number}.pdf"'
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 1000
    assert b"ENAMEL CLINICAL RECORD EXPORT" in response.content

    audit = AuditEvent.objects.get(action="patient.exported", entity_id=str(patient.id))
    assert audit.after["format"] == "pdf"
    assert audit.after["rows"] == 1


def test_export_requires_the_export_permission_and_a_valid_format(
    owner, staff_factory, patient_factory
):
    patient = patient_factory(owner.clinic)

    assistant = staff_factory(clinic=owner.clinic, role="assistant")
    denied = assistant.get(f"/api/v1/patients/{patient.id}/export/")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "permission_denied"

    receptionist = staff_factory(clinic=owner.clinic, role="receptionist")
    assert receptionist.get(f"/api/v1/patients/{patient.id}/export/").status_code == 403

    bad_format = owner.get(f"/api/v1/patients/{patient.id}/export/", {"format": "csv"})
    assert bad_format.status_code == 400
    assert "format" in bad_format.json()["error"]["field_errors"]


def test_sixth_export_in_an_hour_is_throttled(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    url = f"/api/v1/patients/{patient.id}/export/"

    for _ in range(5):
        assert owner.get(url, {"format": "json"}).status_code == 200

    throttled = owner.get(url, {"format": "json"})
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "throttled"
    assert int(throttled["Retry-After"]) > 0


def test_export_of_a_foreign_patient_is_404(staff_factory, owner, patient_factory):
    intruder = staff_factory(role="owner")
    patient = patient_factory(owner.clinic)
    response = intruder.get(f"/api/v1/patients/{patient.id}/export/")
    assert response.status_code == 404
    assert not AuditEvent.objects.filter(
        action="patient.exported", entity_id=str(patient.id)
    ).exists()


def test_export_unknown_patient_is_404(owner):
    response = owner.get(f"/api/v1/patients/{uuid.uuid4()}/export/")
    assert response.status_code == 404
