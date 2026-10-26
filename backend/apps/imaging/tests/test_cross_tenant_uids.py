"""Cross-tenant identifier handling in the imaging archive (F-12).

A Study/Series/Instance Instance UID is looked up globally, so an upload can
collide with an object that belongs to another clinic. The archive must not
turn that collision into an existence oracle: the response has to be the same
opaque "not found" that an unknown identifier produces, and it must never name
another tenant.
"""

from __future__ import annotations

import pytest

from apps.imaging.models import Instance, Series, Study

from .conftest import build_upload

pytestmark = pytest.mark.django_db

STUDIES_URL = "/api/v1/imaging/studies/"


def test_foreign_study_uid_is_opaque(owner, patient_factory, staff_factory):
    other = staff_factory(role="owner", slug="other-imaging-clinic")
    foreign_patient = patient_factory(other.clinic, first_name="Foreign")

    # The other clinic owns the study/series/instance UIDs first.
    seeded = other.post_multipart(
        STUDIES_URL,
        {
            "patient": str(foreign_patient.id),
            "files": [build_upload("study1_series1_instance1.dcm")],
        },
    )
    assert seeded.status_code == 201, seeded.content

    patient = patient_factory(owner.clinic)
    response = owner.post_multipart(
        STUDIES_URL,
        {
            "patient": str(patient.id),
            "files": [build_upload("study1_series1_instance1.dcm")],
        },
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    # The message must not reveal that the identifier exists in another clinic.
    assert "another clinic" not in error["message"]
    assert "another" not in error["message"]
    # Nothing was written for the requesting clinic.
    assert Study.all_objects.filter(clinic_id=owner.clinic.id).count() == 0
    assert Series.all_objects.filter(clinic_id=owner.clinic.id).count() == 0
    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 0


def test_foreign_sop_instance_uid_is_opaque(owner, patient_factory, staff_factory):
    other = staff_factory(role="owner", slug="other-imaging-clinic-2")
    foreign_patient = patient_factory(other.clinic, first_name="Foreign")

    seeded = other.post_multipart(
        STUDIES_URL,
        {
            "patient": str(foreign_patient.id),
            "files": [build_upload("study1_series1_instance1.dcm")],
        },
    )
    assert seeded.status_code == 201, seeded.content

    # A different study/series with the same SOP Instance UID: the instance
    # collision is the one that is detected first.
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.imaging.dicom.deid import serialize_dataset
    from apps.imaging.dicom.fixtures import build_dataset

    dataset = build_dataset(
        study_uid="1.2.826.0.1.3680043.10.999.9",
        series_uid="1.2.826.0.1.3680043.10.999.9.1",
        sop_uid="1.2.826.0.1.3680043.10.999.1.1.1",
        modality="IO",
        series_modality="IO",
        body_part="TEETH",
        study_description="Foreign instance",
        series_description="Foreign instance",
        accession="ACC-FOREIGN",
        instance_number=1,
        seed=4242,
    )
    upload = SimpleUploadedFile(
        "foreign.dcm", serialize_dataset(dataset), content_type="application/dicom"
    )

    patient = patient_factory(owner.clinic)
    response = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [upload]}
    )

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "another clinic" not in error["message"]
    assert Instance.all_objects.filter(clinic_id=owner.clinic.id).count() == 0


def test_same_clinic_duplicate_content_still_conflicts(owner, patient_factory):
    """The in-tenant conflict is unchanged: it is a real 409, not a 404."""

    patient = patient_factory(owner.clinic)
    first = owner.post_multipart(
        STUDIES_URL,
        {"patient": str(patient.id), "files": [build_upload("study1_series1_instance1.dcm")]},
    )
    assert first.status_code == 201

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.imaging.dicom.deid import serialize_dataset
    from apps.imaging.dicom.fixtures import build_dataset

    dataset = build_dataset(
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
    upload = SimpleUploadedFile(
        "altered.dcm", serialize_dataset(dataset), content_type="application/dicom"
    )

    response = owner.post_multipart(
        STUDIES_URL, {"patient": str(patient.id), "files": [upload]}
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
