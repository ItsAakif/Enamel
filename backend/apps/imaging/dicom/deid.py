"""Configurable DICOM de-identification (T127).

The tag list comes from ``settings.IMAGING_DEID_TAGS`` and defaults to the
patient/institution/physician identifier set documented in
``config/settings/base.py``. Only the *metadata* is touched: ``PixelData`` and
every pixel-describing attribute (``Rows``, ``Columns``, ``BitsAllocated``,
``PhotometricInterpretation``, the transfer syntax) are preserved byte for byte.
:func:`deidentify` returns the SHA-256 of the pixel data before and after the
edit so the caller can assert the pixel payload was not altered.

The removed keyword names are returned (never their values) and are the only
de-identification record that reaches the database.

Known limitations
-----------------
These are properties of the design, not bugs, and they have to be disclosed to
the operator (see ``backend/README.md`` and the upload dialog in the SPA):

* **Burned-in annotations are not removed.** Text or graphics rendered into the
  pixel data itself (a patient name in the corner of a screenshot, a scanned
  referral letter) survive de-identification untouched, because "pixel data is
  never touched" is a stated requirement. The source system has to de-identify
  the image before it is uploaded.
* **Nested sequences are not scrubbed.** The default tag list removes
  *top-level* attributes only. A sequence element such as
  ``OtherPatientIDsSequence`` or ``ReferencedPatientSequence`` keeps its
  content unless the operator extends ``IMAGING_DEID_TAGS`` with the matching
  sequence keywords.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pydicom
from django.conf import settings
from pydicom.datadict import keyword_dict

#: Fallback used when ``IMAGING_DEID_TAGS`` is not configured. Documented in
#: ``ARCHITECTURE.md`` and ``config/settings/base.py``.
DEFAULT_DEID_TAGS: tuple[str, ...] = (
    "PatientName",
    "PatientID",
    "IssuerOfPatientID",
    "OtherPatientIDs",
    "OtherPatientNames",
    "PatientBirthDate",
    "PatientBirthTime",
    "PatientSex",
    "PatientAge",
    "PatientSize",
    "PatientWeight",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "PatientMotherBirthName",
    "PatientBirthName",
    "PatientComments",
    "AccessionNumber",
    "AccessionComments",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "ReferringPhysicianName",
    "ReferringPhysicianAddress",
    "ReferringPhysicianTelephoneNumbers",
    "PerformingPhysicianName",
    "OperatorsName",
    "PhysiciansOfRecord",
    "RequestingPhysician",
    "DeviceSerialNumber",
    "StationName",
    "StudyID",
)


def deid_tags() -> tuple[str, ...]:
    """Return the configured de-identification keyword list."""

    configured = getattr(settings, "IMAGING_DEID_TAGS", None)
    if not configured:
        return DEFAULT_DEID_TAGS
    return tuple(str(item).strip() for item in configured if str(item).strip())


def pixel_data_sha256(dataset: Any) -> str:
    """SHA-256 of the raw ``PixelData`` element (empty string when absent)."""

    raw = getattr(dataset, "PixelData", None)
    if raw is None:
        return ""
    return hashlib.sha256(bytes(raw)).hexdigest()


@dataclass(frozen=True)
class DeidResult:
    """Outcome of one de-identification pass."""

    removed: tuple[str, ...]
    pixel_sha256_before: str
    pixel_sha256_after: str

    @property
    def pixel_unchanged(self) -> bool:
        return self.pixel_sha256_before == self.pixel_sha256_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed_tags": list(self.removed),
            "pixel_sha256": self.pixel_sha256_after,
            "pixel_unchanged": self.pixel_unchanged,
        }


def _present(dataset: Any, keyword: str) -> bool:
    # Unknown keywords (for example the documented "AccessionComments", which
    # has no standard tag) are ignored rather than passed to pydicom, which
    # would log a warning for every instance.
    if keyword not in keyword_dict:
        return False
    try:
        return keyword in dataset
    except Exception:  # noqa: BLE001 - a malformed dataset simply lacks the tag
        return False


def deidentify(dataset: Any, *, tags: tuple[str, ...] | None = None) -> DeidResult:
    """Remove the configured identifier tags from ``dataset`` in place."""

    keywords = tags if tags is not None else deid_tags()
    before = pixel_data_sha256(dataset)

    removed: list[str] = []
    for keyword in keywords:
        if not _present(dataset, keyword):
            continue
        try:
            del dataset[keyword]
        except Exception:  # noqa: BLE001 - a tag pydicom refuses to drop stays
            continue
        removed.append(keyword)

    after = pixel_data_sha256(dataset)
    return DeidResult(
        removed=tuple(removed),
        pixel_sha256_before=before,
        pixel_sha256_after=after,
    )


def serialize_dataset(dataset: Any) -> bytes:
    """Serialise a de-identified dataset back to DICOM bytes deterministically."""

    buffer = BytesIO()
    pydicom.dcmwrite(buffer, dataset, enforce_file_format=True)
    return buffer.getvalue()


__all__ = [
    "DEFAULT_DEID_TAGS",
    "DeidResult",
    "deid_tags",
    "deidentify",
    "pixel_data_sha256",
    "serialize_dataset",
]
