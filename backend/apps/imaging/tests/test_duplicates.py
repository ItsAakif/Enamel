"""Duplicate SOP Instance handling (T127)."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.imaging import storage
from apps.imaging.dicom.deid import serialize_dataset
from apps.imaging.dicom.fixtures import build_dataset, build_fixtures
from apps.imaging.models import Instance

from .conftest import build_upload

pytestmark = pytest.mark.django_db

STUDIES_URL = "/api/v1/imaging/studies/"


def test_identical_duplicate_upload_is_idempotent(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    payload = {
        "patient": str(patient.id),
        "files": [build_upload("study1_series1_instance1.dcm")],
    }

    first = owner.post_multipart(STUDIES_URL, payload)
    second = owner.post_multipart(
        STUDIES_URL,
        {"patient": str(patient.id), "files": [build_upload("study1_series1_instance1.dcm")]},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 1
    assert Instance.all_objects.get(clinic_id=owner.clinic.id).sop_instance_uid == (
        "1.2.826.0.1.3680043.10.999.1.1.1"
    )


def test_same_sop_uid_with_different_content_conflicts(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    first = owner.post_multipart(
        STUDIES_URL,
        {"patient": str(patient.id), "files": [build_upload("study1_series1_instance1.dcm")]},
    )
    assert first.status_code == 201

    # Same SOP UID, different pixel content -> a different digest.
    altered = build_dataset(
        study_uid="1.2.826.0.1.3680043.10.999.1",
        series_uid="1.2.826.0.1.3680043.10.999.1.1",
        sop_uid="1.2.826.0.1.3680043.10.999.1.1.1",
        modality="IO",
        series_modality="IO",
        body_part="TEETH",
        study_description="Altered",
        series_description="Altered",
        accession="ACC-ALTERED",
        instance_number=1,
        seed=999,
    )
    altered_bytes = serialize_dataset(altered)
    upload = SimpleUploadedFile("altered.dcm", altered_bytes, content_type="application/dicom")

    response = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [upload]}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 1


def test_reupload_of_a_multi_file_batch_adds_nothing(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    names = [
        "study1_series1_instance1.dcm",
        "study1_series1_instance2.dcm",
        "study1_series2_instance1.dcm",
    ]
    first = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [build_upload(n) for n in names]}
    )
    second = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [build_upload(n) for n in names]}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 3


def test_idempotent_reupload_restores_missing_stored_bytes(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    payload = {
        "patient": str(patient.id),
        "files": [build_upload("study1_series1_instance1.dcm")],
    }
    assert owner.post_multipart(STUDIES_URL, payload).status_code == 201

    instance = Instance.all_objects.get(clinic_id=owner.clinic.id)
    storage.default_storage.delete(instance.storage_key)
    storage.default_storage.delete(instance.preview_key)
    assert not storage.exists(instance.storage_key)
    assert not storage.exists(instance.preview_key)

    healed = owner.post_multipart(
        STUDIES_URL,
        {"patient": str(patient.id), "files": [build_upload("study1_series1_instance1.dcm")]},
    )

    assert healed.status_code == 201
    instance.refresh_from_db()
    assert storage.exists(instance.storage_key)
    assert storage.exists(instance.preview_key)
    preview = owner.get(f"/api/v1/imaging/instances/{instance.id}/preview/")
    assert preview.status_code == 200
    assert preview.content[:2] == b"\xff\xd8"


def test_fixture_bytes_are_stable_across_builds():
    assert build_fixtures() == build_fixtures()
