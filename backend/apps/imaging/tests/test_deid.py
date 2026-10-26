"""De-identification: tags removed, pixel data preserved (T127)."""

from __future__ import annotations

import pytest

from apps.imaging import storage
from apps.imaging.dicom.deid import (
    DEFAULT_DEID_TAGS,
    deidentify,
    pixel_data_sha256,
)
from apps.imaging.dicom.parse import parse_dicom
from apps.imaging.models import Instance

pytestmark = pytest.mark.django_db


def _parse(dicom_files, name="study1_series1_instance1.dcm"):
    return parse_dicom(dicom_files[name])


def test_deidentify_removes_configured_tags_and_preserves_pixels(dicom_files):
    dataset = _parse(dicom_files)
    before = pixel_data_sha256(dataset)

    result = deidentify(dataset)

    assert result.pixel_unchanged is True
    assert result.pixel_sha256_before == before
    assert "PatientName" in result.removed
    assert "PatientID" in result.removed
    assert "PatientBirthDate" in result.removed
    assert "AccessionNumber" in result.removed
    assert not hasattr(dataset, "PatientName")
    # Hierarchy identifiers are never de-identified.
    assert dataset.StudyInstanceUID
    assert dataset.SOPInstanceUID


def test_default_tag_list_is_documented(dicom_files):
    for keyword in ("PatientName", "PatientID", "PatientBirthDate", "PatientSex"):
        assert keyword in DEFAULT_DEID_TAGS


def test_configured_tag_list_is_honoured(dicom_files, settings):
    settings.IMAGING_DEID_TAGS = ["PatientName"]
    dataset = _parse(dicom_files)

    result = deidentify(dataset)

    assert result.removed == ("PatientName",)
    assert not hasattr(dataset, "PatientName")
    assert hasattr(dataset, "PatientID")  # not in the configured list


def test_stored_instance_carries_no_phi_but_keeps_pixel_hash(owner, upload_study, dicom_files):
    response, _ = upload_study("study1_series1_instance1.dcm")
    assert response.status_code == 201

    instance = Instance.all_objects.get(sop_instance_uid="1.2.826.0.1.3680043.10.999.1.1.1")
    original = _parse(dicom_files)
    original_pixel_hash = pixel_data_sha256(original)

    stored = parse_dicom(storage.read_bytes(instance.storage_key))
    assert not hasattr(stored, "PatientName")
    assert not hasattr(stored, "PatientID")
    assert not hasattr(stored, "PatientAddress")

    assert instance.deid_json["pixel_sha256"] == original_pixel_hash
    assert instance.deid_json["pixel_unchanged"] is True
    assert "PatientName" in instance.deid_json["removed_tags"]
    # The stored bytes hash to the recorded content digest.
    import hashlib

    assert hashlib.sha256(storage.read_bytes(instance.storage_key)).hexdigest() == instance.sha256
