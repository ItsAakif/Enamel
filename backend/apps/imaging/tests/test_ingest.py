"""Study ingest: metadata, quota, caps and audit (T127)."""

from __future__ import annotations

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.models import AuditEvent
from apps.imaging.dicom.fixtures import build_fixtures, write_fixtures
from apps.imaging.models import Instance, Series, Study

from .conftest import build_upload

pytestmark = pytest.mark.django_db

STUDIES_URL = "/api/v1/imaging/studies/"


def test_upload_creates_study_series_instance_with_metadata(owner, upload_study):
    response, patient = upload_study("study1_series1_instance1.dcm")

    assert response.status_code == 201, response.json()
    body = response.json()
    assert len(body) == 1
    assert body[0]["study_instance_uid"] == "1.2.826.0.1.3680043.10.999.1"
    assert body[0]["series_count"] == 1
    assert body[0]["instance_count"] == 1

    study = Study.all_objects.get(study_instance_uid="1.2.826.0.1.3680043.10.999.1")
    assert study.clinic_id == owner.clinic.id
    assert study.patient_id == patient.id
    assert study.modality == "IO"
    assert study.study_date == date(2026, 1, 1)

    series = Series.all_objects.get(series_instance_uid="1.2.826.0.1.3680043.10.999.1.1")
    assert series.study_id == study.id
    assert series.instance_count == 1

    instance = Instance.all_objects.get(sop_instance_uid="1.2.826.0.1.3680043.10.999.1.1.1")
    assert instance.series_id == series.id
    assert instance.rows == 32
    assert instance.columns == 32
    assert instance.bits_allocated == 16
    assert instance.photometric == "MONOCHROME2"
    assert instance.transfer_syntax == "1.2.840.10008.1.2.1"
    assert instance.size_bytes > 0
    assert instance.preview_status == Instance.PreviewStatus.READY


def test_multi_series_study_preserves_every_series(owner, upload_study):
    response, _ = upload_study(
        "study1_series1_instance1.dcm",
        "study1_series1_instance2.dcm",
        "study1_series2_instance1.dcm",
    )

    assert response.status_code == 201
    assert len(response.json()) == 1  # all three files belong to one study
    study = Study.all_objects.get(study_instance_uid="1.2.826.0.1.3680043.10.999.1")
    assert Series.all_objects.filter(study=study).count() == 2
    assert Instance.all_objects.filter(series__study=study).count() == 3
    series_one = Series.all_objects.get(series_instance_uid="1.2.826.0.1.3680043.10.999.1.1")
    assert series_one.instance_count == 2


def test_upload_writes_audit_rows_with_actor_and_clinic(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201

    events = AuditEvent.objects.filter(
        clinic_id=owner.clinic.id, action="imaging.instance_uploaded"
    )
    assert events.count() == 1
    event = events.first()
    assert event.actor_id == owner.user.id
    assert event.entity_type == "imaging.instance"
    assert event.after["sha256"]


def test_non_dicom_upload_is_rejected_with_invalid_dicom(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    not_dicom = SimpleUploadedFile(
        "not_dicom.dcm", b"\x89PNG\r\n\x1a\nnot-a-dicom-object", content_type="application/dicom"
    )
    response = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [not_dicom]}
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_dicom"
    assert Instance.all_objects.count() == 0


def test_oversize_upload_is_rejected_before_storage(owner, patient_factory, settings):
    settings.IMAGING_MAX_BYTES = 16
    patient = patient_factory(owner.clinic)
    response = owner.post_multipart(
        STUDIES_URL,
        {"patient": str(patient.id), "files": [build_upload("study1_series1_instance1.dcm")]},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert Instance.all_objects.count() == 0


def test_daily_quota_is_enforced(owner, patient_factory, settings):
    settings.IMAGING_DAILY_INSTANCE_QUOTA = 1
    patient = patient_factory(owner.clinic)
    response = owner.post_multipart(
        STUDIES_URL,
        {
            "patient": str(patient.id),
            "files": [
                build_upload("study1_series1_instance1.dcm"),
                build_upload("study1_series1_instance2.dcm"),
            ],
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "throttled"
    assert Instance.all_objects.count() == 1


def test_upload_requires_a_clinic_patient(owner):
    response = owner.post_multipart(
        STUDIES_URL, {"files": [build_upload("study1_series1_instance1.dcm")]}
    )
    assert response.status_code == 400
    assert "patient" in response.json()["error"]["field_errors"]


def test_foreign_patient_is_rejected(owner, staff_factory):
    other = staff_factory(role="owner", slug="other-imaging-clinic")
    from apps.patients.models import Patient

    foreign_patient = Patient.all_objects.create(
        clinic=other.clinic,
        number="EN-000001",
        first_name="Foreign",
        last_name="Patient",
    )
    response = owner.post_multipart(
        STUDIES_URL,
        {
            "patient": str(foreign_patient.id),
            "files": [build_upload("study1_series1_instance1.dcm")],
        },
    )
    assert response.status_code == 400
    assert Instance.all_objects.count() == 0


def test_fixture_generation_is_deterministic(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    write_fixtures(first)
    write_fixtures(second)

    names = sorted(build_fixtures())
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert (first / "README.md").read_bytes() == (second / "README.md").read_bytes()
    assert "Licence" in (first / "README.md").read_text(encoding="utf-8")


def test_committed_fixtures_match_the_generator(tmp_path):
    from django.conf import settings

    committed = settings.BASE_DIR / "fixtures" / "dicom"
    generated = tmp_path / "dicom"
    write_fixtures(generated)
    for name in build_fixtures():
        assert (committed / name).read_bytes() == (generated / name).read_bytes()
