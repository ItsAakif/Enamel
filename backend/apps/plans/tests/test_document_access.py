"""Document upload validation and signed download access (T117)."""

from __future__ import annotations

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.plans.models import Document

pytestmark = pytest.mark.django_db

DOCUMENTS = "/api/v1/documents/"


def _walk(prefix: str = "") -> list[str]:
    out: list[str] = []
    try:
        dirs, files = default_storage.listdir(prefix)
    except FileNotFoundError:
        return out
    for name in files:
        out.append(f"{prefix}/{name}" if prefix else name)
    for directory in dirs:
        out.extend(_walk(f"{prefix}/{directory}" if prefix else directory))
    return out


def _upload(client, *, filename="scan.pdf", content=b"%PDF-1.4 hello", content_type="application/pdf", **extra):
    return client.post(
        DOCUMENTS,
        {"file": SimpleUploadedFile(filename, content, content_type=content_type), **extra},
        format="multipart",
    )


def test_upload_and_download_with_signed_url(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    created = _upload(owner, patient=str(patient.id), kind="xray")
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["sha256"]
    assert body["storage_key"].startswith(f"clinic/{owner.clinic.id}/patient/{patient.id}/")
    assert "token=" in body["download_url"]

    downloaded = owner.get(body["download_url"])
    assert downloaded.status_code == 200
    assert downloaded["Content-Type"] == "application/pdf"
    assert downloaded.content == b"%PDF-1.4 hello"


def test_download_without_token_is_forbidden(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    document_id = _upload(owner, patient=str(patient.id)).json()["id"]
    response = owner.get(f"{DOCUMENTS}{document_id}/download/")
    assert response.status_code == 403


def test_download_with_tampered_token_is_forbidden(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    body = _upload(owner, patient=str(patient.id)).json()
    tampered = body["download_url"].replace("token=", "token=x", 1)
    response = owner.get(tampered)
    assert response.status_code == 403


def test_download_requires_authentication(owner, patient_factory, api_client):
    patient = patient_factory(owner.clinic)
    body = _upload(owner, patient=str(patient.id)).json()
    anonymous = api_client.get(body["download_url"])
    assert anonymous.status_code == 401


def test_cross_clinic_download_is_404(owner, patient_factory, staff_factory):
    patient = patient_factory(owner.clinic)
    body = _upload(owner, patient=str(patient.id)).json()

    other = staff_factory(role="owner")
    response = other.get(body["download_url"])
    assert response.status_code == 404


def test_oversize_upload_is_rejected_before_storage(owner, patient_factory, settings):
    settings.DOCUMENT_MAX_BYTES = 1024
    patient = patient_factory(owner.clinic)
    response = _upload(owner, content=b"x" * 4096, patient=str(patient.id))
    assert response.status_code == 413
    assert Document.all_objects.filter(clinic=owner.clinic).count() == 0
    assert _walk(f"clinic/{owner.clinic.id}") == []


def test_wrong_mime_is_rejected_before_storage(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    response = _upload(
        owner,
        filename="evil.exe",
        content=b"MZ",
        content_type="application/x-msdownload",
        patient=str(patient.id),
    )
    assert response.status_code == 415
    assert Document.all_objects.filter(clinic=owner.clinic).count() == 0
    assert _walk(f"clinic/{owner.clinic.id}") == []


def test_assistant_cannot_upload(assistant, patient_factory):
    patient = patient_factory(assistant.clinic)
    response = _upload(assistant, patient=str(patient.id))
    assert response.status_code == 403


def test_same_content_is_stored_once(owner, patient_factory):
    patient = patient_factory(owner.clinic)
    first = _upload(owner, patient=str(patient.id)).json()
    second = _upload(owner, filename="copy.pdf", patient=str(patient.id)).json()
    assert first["storage_key"] == second["storage_key"]
    files = _walk(f"clinic/{owner.clinic.id}")
    assert len(files) == 1
