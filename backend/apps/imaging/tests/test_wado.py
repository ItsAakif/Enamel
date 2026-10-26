"""WADO/QIDO subset and preview rendering (T127)."""

from __future__ import annotations

import hashlib

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.imaging.dicom.deid import serialize_dataset
from apps.imaging.dicom.fixtures import build_dataset
from apps.imaging.models import Instance, Series, Study

pytestmark = pytest.mark.django_db

STUDIES_URL = "/api/v1/imaging/studies/"


def test_wado_returns_bytes_whose_hash_matches_the_stored_instance(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    instance = Instance.all_objects.get(clinic_id=owner.clinic.id)

    wado = owner.get(f"/api/v1/imaging/dicomweb/instances/{instance.sop_instance_uid}/")

    assert wado.status_code == 200
    assert wado["Content-Type"] == "application/dicom"
    assert wado["X-Content-SHA256"] == instance.sha256
    assert hashlib.sha256(wado.content).hexdigest() == instance.sha256


def test_preview_returns_a_jpeg(owner, upload_study):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201
    instance = Instance.all_objects.get(clinic_id=owner.clinic.id)
    assert instance.preview_status == Instance.PreviewStatus.READY

    preview = owner.get(f"/api/v1/imaging/instances/{instance.id}/preview/")

    assert preview.status_code == 200
    assert preview["Content-Type"] == "image/jpeg"
    assert preview.content[:2] == b"\xff\xd8"


def test_preview_failure_is_recorded_not_silently_empty(owner, patient_factory):
    # A DICOM object with no pixel data cannot be previewed.
    dataset = build_dataset(
        study_uid="1.2.826.0.1.3680043.10.999.9",
        series_uid="1.2.826.0.1.3680043.10.999.9.1",
        sop_uid="1.2.826.0.1.3680043.10.999.9.1.1",
        modality="IO",
        series_modality="IO",
        body_part="TEETH",
        study_description="No pixel data",
        series_description="No pixel data",
        accession="ACC-NOPIXEL",
        instance_number=1,
        seed=1,
    )
    del dataset.PixelData
    data = serialize_dataset(dataset)

    patient = patient_factory(owner.clinic)
    upload = SimpleUploadedFile("nopixel.dcm", data, content_type="application/dicom")
    response = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [upload]}
    )
    assert response.status_code == 201, response.json()

    instance = Instance.all_objects.get(sop_instance_uid="1.2.826.0.1.3680043.10.999.9.1.1")
    assert instance.preview_status == Instance.PreviewStatus.FAILED
    assert instance.preview_key is None

    preview = owner.get(f"/api/v1/imaging/instances/{instance.id}/preview/")
    assert preview.status_code == 404
    assert preview.json()["error"]["details"]["preview_status"] == "failed"


def test_qido_studies_and_series(owner, upload_study):
    response, _ = upload_study(
        "study1_series1_instance1.dcm",
        "study1_series1_instance2.dcm",
        "study1_series2_instance1.dcm",
    )
    assert response.status_code == 201

    studies = owner.get("/api/v1/imaging/dicomweb/studies/")
    assert studies.status_code == 200
    body = studies.json()
    assert len(body) == 1
    row = body[0]
    assert row["0020000D"]["Value"] == ["1.2.826.0.1.3680043.10.999.1"]
    assert row["00201206"]["Value"] == [2]  # two series
    assert row["00201208"]["Value"] == [3]  # three instances
    assert set(row["00080061"]["Value"]) == {"IO", "DX"}

    series = owner.get(
        "/api/v1/imaging/dicomweb/studies/1.2.826.0.1.3680043.10.999.1/series/"
    )
    assert series.status_code == 200
    uids = {item["0020000E"]["Value"][0] for item in series.json()}
    assert uids == {
        "1.2.826.0.1.3680043.10.999.1.1",
        "1.2.826.0.1.3680043.10.999.1.2",
    }


def test_study_detail_lists_series_and_instances(owner, upload_study):
    response, _ = upload_study(
        "study1_series1_instance1.dcm", "study1_series2_instance1.dcm"
    )
    assert response.status_code == 201
    study = Study.all_objects.get(clinic_id=owner.clinic.id)

    detail = owner.get(f"/api/v1/imaging/studies/{study.id}/")
    assert detail.status_code == 200
    body = detail.json()
    assert body["study_instance_uid"] == study.study_instance_uid
    assert len(body["series"]) == 2
    assert all("instances" in series for series in body["series"])
    assert "storage_key" not in body["series"][0]["instances"][0]


def test_series_instance_count_is_consistent(owner, upload_study):
    response, _ = upload_study(
        "study1_series1_instance1.dcm", "study1_series1_instance2.dcm"
    )
    assert response.status_code == 201
    series = Series.all_objects.get(series_instance_uid="1.2.826.0.1.3680043.10.999.1.1")
    assert series.instance_count == 2
