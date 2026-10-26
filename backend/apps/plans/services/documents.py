"""Shared document store (T117).

Uploads are validated *before* anything touches storage: a file larger than
``DOCUMENT_MAX_BYTES`` (25 MB by default) is rejected with 413, and a content
type outside ``DOCUMENT_ALLOWED_MIME`` with 415. Accepted bytes are stored
content-addressed at ``clinic/<id>/patient/<id>/<sha256>`` so identical content
is stored once.

Download is never a raw media URL. The API issues a short-lived signed token
(``DOCUMENT_DOWNLOAD_TTL_SECONDS``) after the caller's permission has been
checked; the download endpoint re-checks permission *and* verifies the token, so
a leaked object key alone cannot read a file.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Document

DOWNLOAD_SALT = "plans.document.download"
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_ALLOWED_MIME = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "text/plain",
)
DEFAULT_DOWNLOAD_TTL_SECONDS = 300


def max_bytes() -> int:
    return int(getattr(settings, "DOCUMENT_MAX_BYTES", DEFAULT_MAX_BYTES))


def allowed_mime() -> frozenset[str]:
    configured = getattr(settings, "DOCUMENT_ALLOWED_MIME", None)
    if not configured:
        configured = DEFAULT_ALLOWED_MIME
    return frozenset(str(item).strip().lower() for item in configured if str(item).strip())


def download_ttl_seconds() -> int:
    return int(
        getattr(settings, "DOCUMENT_DOWNLOAD_TTL_SECONDS", DEFAULT_DOWNLOAD_TTL_SECONDS)
    )


def storage_key(clinic_id: Any, patient_id: Any, sha256: str) -> str:
    """Return the content-addressed key for one clinic/patient/content tuple."""

    patient = str(patient_id) if patient_id else "none"
    return f"clinic/{clinic_id}/patient/{patient}/{sha256}"


def validate_upload(*, content_type: str, size: int) -> None:
    """Reject an oversize or disallowed upload before it reaches storage."""

    if size > max_bytes():
        raise ApiError(
            "payload_too_large",
            "The uploaded file is too large.",
            status_code=413,
            details={"max_bytes": max_bytes()},
        )
    if (content_type or "").strip().lower() not in allowed_mime():
        raise ApiError(
            "unsupported_media_type",
            "This file type is not allowed.",
            status_code=415,
            details={"allowed": sorted(allowed_mime())},
        )


def upload(
    *,
    clinic: Any,
    patient: Any,
    kind: str,
    filename: str,
    content_type: str,
    data: bytes,
    uploaded_by: Any = None,
    plan: Any = None,
    consent: Any = None,
    actor: Any = None,
    request: Any = None,
) -> Document:
    """Validate, store and record one document."""

    validate_upload(content_type=content_type, size=len(data))
    digest = hashlib.sha256(data).hexdigest()
    key = storage_key(clinic.id, getattr(patient, "id", None), digest)

    with with_clinic(clinic):
        if not default_storage.exists(key):
            default_storage.save(key, ContentFile(data))
        document = Document.all_objects.create(
            clinic_id=clinic.id,
            patient=patient,
            kind=(kind or "other")[:64],
            filename=filename[:255],
            content_type=content_type[:128],
            size_bytes=len(data),
            sha256=digest,
            storage_key=key,
            uploaded_by=uploaded_by,
            plan=plan,
            consent=consent,
        )
        record_audit(
            "document.uploaded",
            entity=document,
            after={
                "kind": document.kind,
                "content_type": document.content_type,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
            },
            actor=actor if actor is not None else uploaded_by,
            request=request,
        )
    return document


def read_bytes(document: Document) -> bytes:
    """Return the stored bytes for ``document``."""

    with default_storage.open(document.storage_key, "rb") as handle:
        return handle.read()


def issue_download_token(document: Document) -> str:
    """Return a signed, short-lived token for ``document``."""

    return signing.dumps(
        {"doc": str(document.pk), "clinic": str(document.clinic_id)},
        salt=DOWNLOAD_SALT,
        compress=True,
    )


def verify_download_token(token: str, *, document: Document) -> bool:
    """True when ``token`` is valid, unexpired and bound to ``document``."""

    if not token:
        return False
    try:
        payload = signing.loads(
            token, salt=DOWNLOAD_SALT, max_age=download_ttl_seconds()
        )
    except signing.SignatureExpired:
        return False
    except signing.BadSignature:
        return False
    except Exception:  # pragma: no cover - defensive: any malformed token
        return False
    return (
        str(payload.get("doc")) == str(document.pk)
        and str(payload.get("clinic")) == str(document.clinic_id)
    )


def download_path(document: Document) -> str:
    """Return the API path for a document download (without the token)."""

    return f"/api/v1/documents/{document.pk}/download/"


__all__ = [
    "DEFAULT_ALLOWED_MIME",
    "DEFAULT_DOWNLOAD_TTL_SECONDS",
    "DEFAULT_MAX_BYTES",
    "DOWNLOAD_SALT",
    "allowed_mime",
    "download_path",
    "download_ttl_seconds",
    "issue_download_token",
    "max_bytes",
    "read_bytes",
    "storage_key",
    "upload",
    "validate_upload",
    "verify_download_token",
]
