"""Content-addressed storage for imaging bytes (T127).

Keys are derived *only* from server-side identifiers: the clinic id, the patient
id, the first two hex characters of the content digest and a random UUID. No
part of an uploaded filename or any client-supplied string can influence the
path, so an upload can never escape its tenant prefix.

Layout (mirrors the ARCHITECTURE.md file-storage convention)::

    clinic/<clinic>/patient/<patient>/<sha256[:2]>/<uuid>.dcm
    clinic/<clinic>/patient/<patient>/<sha256[:2]>/<uuid>.jpg   (preview)
"""

from __future__ import annotations

import uuid
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def _segment(value: Any) -> str:
    """Return a path-safe segment for a server-side identifier."""

    text = str(value) if value is not None else "none"
    # Defensive: strip anything that is not a UUID/hex character. Server-side
    # ids are always UUIDs, so this never changes a legitimate value.
    cleaned = "".join(char for char in text if char.isalnum() or char == "-")
    return cleaned or "none"


def storage_key(clinic_id: Any, patient_id: Any, sha256: str, *, suffix: str = ".dcm") -> str:
    """Return the content-addressed key for one instance's bytes."""

    prefix = (sha256 or "00")[:2].lower()
    name = f"{uuid.uuid4().hex}{suffix}"
    return (
        f"clinic/{_segment(clinic_id)}/patient/{_segment(patient_id)}/{prefix}/{name}"
    )


def save_bytes(key: str, data: bytes) -> str:
    """Persist ``data`` at ``key`` (idempotent when the key already exists)."""

    if not default_storage.exists(key):
        default_storage.save(key, ContentFile(data))
    return key


def read_bytes(key: str) -> bytes:
    """Return the stored bytes at ``key``."""

    with default_storage.open(key, "rb") as handle:
        return handle.read()


def exists(key: str) -> bool:
    return bool(key) and default_storage.exists(key)


__all__ = ["exists", "read_bytes", "save_bytes", "storage_key"]
