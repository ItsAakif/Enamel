"""DICOM validation and metadata extraction (T127).

A file is only treated as DICOM when the four magic bytes ``DICM`` appear at
offset 128 (the DICOM preamble contract) *and* pydicom can parse it. A JPEG or
PNG renamed to ``.dcm`` therefore fails both checks and is rejected as
``invalid_dicom`` -- it is never guessed from the filename or content type.

Only the metadata needed for the study/series/instance hierarchy is extracted.
Patient identifiers are read here so they can be removed by
:mod:`apps.imaging.dicom.deid`; they are never persisted.
"""

from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any

import pydicom

PREAMBLE_LENGTH = 128
MAGIC = b"DICM"


class InvalidDicomError(ValueError):
    """Raised when bytes are not a parseable DICOM object."""


def is_dicom(data: bytes) -> bool:
    """True when ``data`` carries the DICOM preamble magic bytes."""

    if not data or len(data) < PREAMBLE_LENGTH + len(MAGIC):
        return False
    return data[PREAMBLE_LENGTH : PREAMBLE_LENGTH + len(MAGIC)] == MAGIC


def parse_dicom(data: bytes) -> Any:
    """Parse ``data`` into a pydicom ``Dataset`` or raise ``InvalidDicomError``."""

    if not is_dicom(data):
        raise InvalidDicomError("The file is not a DICOM object (missing DICM magic).")
    try:
        dataset = pydicom.dcmread(BytesIO(data), force=False)
    except Exception as exc:  # noqa: BLE001 - any pydicom failure is invalid input
        raise InvalidDicomError("The DICOM object could not be parsed.") from exc

    if not getattr(dataset, "SOPInstanceUID", None):
        raise InvalidDicomError("The DICOM object has no SOPInstanceUID.")
    return dataset


def _text(dataset: Any, keyword: str) -> str:
    value = getattr(dataset, keyword, None)
    if value is None:
        return ""
    return str(value).strip()


def _int(dataset: Any, keyword: str) -> int | None:
    value = getattr(dataset, keyword, None)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_study_date(value: Any) -> date | None:
    """Convert a DICOM ``DA`` value (``YYYYMMDD``) to a ``date`` or ``None``."""

    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def transfer_syntax(dataset: Any) -> str:
    file_meta = getattr(dataset, "file_meta", None)
    uid = getattr(file_meta, "TransferSyntaxUID", None) if file_meta else None
    return str(uid) if uid else ""


def extract_metadata(dataset: Any) -> dict[str, Any]:
    """Return the hierarchy + pixel metadata used to build the archive rows."""

    return {
        "study_instance_uid": _text(dataset, "StudyInstanceUID"),
        "series_instance_uid": _text(dataset, "SeriesInstanceUID"),
        "sop_instance_uid": _text(dataset, "SOPInstanceUID"),
        "accession": _text(dataset, "AccessionNumber"),
        "modality": _text(dataset, "Modality"),
        "study_date": parse_study_date(getattr(dataset, "StudyDate", None)),
        "description": _text(dataset, "StudyDescription"),
        "series_description": _text(dataset, "SeriesDescription"),
        "body_part": _text(dataset, "BodyPartExamined"),
        "instance_number": _int(dataset, "InstanceNumber"),
        "rows": _int(dataset, "Rows"),
        "columns": _int(dataset, "Columns"),
        "bits_allocated": _int(dataset, "BitsAllocated"),
        "photometric": _text(dataset, "PhotometricInterpretation"),
        "transfer_syntax": transfer_syntax(dataset),
    }


__all__ = [
    "MAGIC",
    "PREAMBLE_LENGTH",
    "InvalidDicomError",
    "extract_metadata",
    "is_dicom",
    "parse_dicom",
    "parse_study_date",
    "transfer_syntax",
]
