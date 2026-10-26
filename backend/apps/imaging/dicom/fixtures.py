"""Deterministic DICOM fixture builder (T127).

The fixtures are generated, not downloaded: every UID, date and pixel value is a
constant, so running :func:`write_fixtures` twice produces byte-identical files.
That is what lets the test suite hash a fixture and the WADO test compare it with
the stored instance hash.

The fixtures deliberately carry the identifier attributes that
:mod:`apps.imaging.dicom.deid` removes (patient name/id/birthdate/sex/address/
telephone, institution and referring physician), so the de-identification test
has something real to delete.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage

UID_ROOT = "1.2.826.0.1.3680043.10.999"
STUDY_DATE = date(2026, 1, 1)
STUDY_DATE_STR = "20260101"
IMAGE_SIZE = 32

#: The fixture hierarchy. ``instances`` is the number of SOP instances in the
#: series; the first series of the first study holds two so the instance list is
#: never a single element.
FIXTURE_STUDIES: tuple[dict[str, Any], ...] = (
    {
        "index": 1,
        "study_instance_uid": f"{UID_ROOT}.1",
        "accession": "FIXTURE-ACC-001",
        "modality": "IO",
        "description": "Fixture intraoral study",
        "series": (
            {
                "index": 1,
                "series_instance_uid": f"{UID_ROOT}.1.1",
                "modality": "IO",
                "body_part": "TEETH",
                "description": "Fixture intraoral series",
                "instances": 2,
            },
            {
                "index": 2,
                "series_instance_uid": f"{UID_ROOT}.1.2",
                "modality": "DX",
                "body_part": "SKULL",
                "description": "Fixture panoramic series",
                "instances": 1,
            },
        ),
    },
    {
        "index": 2,
        "study_instance_uid": f"{UID_ROOT}.2",
        "accession": "FIXTURE-ACC-002",
        "modality": "IO",
        "description": "Fixture second study",
        "series": (
            {
                "index": 1,
                "series_instance_uid": f"{UID_ROOT}.2.1",
                "modality": "IO",
                "body_part": "TEETH",
                "description": "Fixture second study series",
                "instances": 1,
            },
        ),
    },
)

#: Identifier attributes written into every fixture (and removed on ingest).
FIXTURE_IDENTIFIERS: dict[str, str] = {
    "PatientName": "Fixture^Patient",
    "PatientID": "FIXTURE-001",
    "PatientBirthDate": "19800101",
    "PatientSex": "O",
    "PatientAddress": "1 Fixture Street",
    "PatientTelephoneNumbers": "+919876500000",
    "InstitutionName": "Fixture Imaging Centre",
    "ReferringPhysicianName": "Referrer^Fixture",
    "StudyID": "FIXTURE-STUDY-01",
}


def sop_instance_uid(study_uid: str, series_uid: str, number: int) -> str:
    return f"{study_uid}.{series_uid.rsplit('.', 1)[-1]}.{number}"


def _pixels(seed: int, size: int = IMAGE_SIZE) -> np.ndarray:
    """Return a deterministic 16-bit synthetic image with a bright blob."""

    ys, xs = np.mgrid[0:size, 0:size]
    center_x = size / 2 + (seed % 5) - 2
    center_y = size / 2
    radius_sq = (xs - center_x) ** 2 + (ys - center_y) ** 2
    blob = np.exp(-radius_sq / (2 * (size / 8) ** 2)) * 800.0
    base = 100.0 + (seed % 7) * 10.0
    return np.clip(base + blob, 0, 4095).astype(np.uint16)


def build_dataset(
    *,
    study_uid: str,
    series_uid: str,
    sop_uid: str,
    modality: str,
    series_modality: str,
    body_part: str,
    study_description: str,
    series_description: str,
    accession: str,
    instance_number: int,
    seed: int,
) -> FileDataset:
    """Build one deterministic, uncompressed DICOM instance."""

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = f"{UID_ROOT}.0.1"
    file_meta.ImplementationVersionName = "ENAMELFIXTURE"

    dataset = FileDataset(
        filename_or_obj=None,
        dataset={},
        file_meta=file_meta,
        preamble=b"\x00" * 128,
    )

    dataset.SOPClassUID = SecondaryCaptureImageStorage
    dataset.SOPInstanceUID = sop_uid
    dataset.StudyInstanceUID = study_uid
    dataset.SeriesInstanceUID = series_uid

    dataset.AccessionNumber = accession
    dataset.Modality = series_modality or modality
    dataset.StudyDate = STUDY_DATE_STR
    dataset.StudyDescription = study_description
    dataset.SeriesDescription = series_description
    dataset.BodyPartExamined = body_part
    dataset.InstanceNumber = instance_number

    for keyword, value in FIXTURE_IDENTIFIERS.items():
        setattr(dataset, keyword, value)

    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.Rows = IMAGE_SIZE
    dataset.Columns = IMAGE_SIZE
    dataset.BitsAllocated = 16
    dataset.BitsStored = 16
    dataset.HighBit = 15
    dataset.PixelRepresentation = 0
    dataset.PixelData = _pixels(seed).tobytes()

    return dataset


def _serialize(dataset: FileDataset) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    pydicom.dcmwrite(buffer, dataset, enforce_file_format=True)
    return buffer.getvalue()


def build_fixtures() -> dict[str, bytes]:
    """Return ``{relative_path: dcm_bytes}`` for every fixture instance."""

    files: dict[str, bytes] = {}
    for study in FIXTURE_STUDIES:
        for series in study["series"]:
            for number in range(1, int(series["instances"]) + 1):
                sop_uid = sop_instance_uid(
                    study["study_instance_uid"], series["series_instance_uid"], number
                )
                seed = study["index"] * 100 + series["index"] * 10 + number
                dataset = build_dataset(
                    study_uid=study["study_instance_uid"],
                    series_uid=series["series_instance_uid"],
                    sop_uid=sop_uid,
                    modality=study["modality"],
                    series_modality=series["modality"],
                    body_part=series["body_part"],
                    study_description=study["description"],
                    series_description=series["description"],
                    accession=study["accession"],
                    instance_number=number,
                    seed=seed,
                )
                name = f"study{study['index']}_series{series['index']}_instance{number}.dcm"
                files[name] = _serialize(dataset)
    return files


def readme_text() -> str:
    """Return the deterministic provenance/licence note for the fixture set."""

    lines = [
        "# DICOM fixtures",
        "",
        "Generated by `python manage.py generate_dicom_fixtures --out fixtures/dicom`.",
        "These files are **synthetic**: no real patient data is present, and none is",
        "derived from a clinical dataset.",
        "",
        "## Provenance",
        "",
        "- Source: generated locally by `apps/imaging/dicom/fixtures.py`.",
        "- Licence: project-internal test data (no redistribution restrictions apply).",
        "- Determinism: every UID, date and pixel value is a constant, so re-running",
        "  the command produces byte-identical files.",
        "- Transfer syntax: Explicit VR Little Endian, uncompressed.",
        "",
        "## Contents",
        "",
    ]
    for study in FIXTURE_STUDIES:
        lines.append(f"- Study `{study['study_instance_uid']}` ({study['description']})")
        for series in study["series"]:
            for number in range(1, int(series["instances"]) + 1):
                sop_uid = sop_instance_uid(
                    study["study_instance_uid"], series["series_instance_uid"], number
                )
                name = (
                    f"study{study['index']}_series{series['index']}_instance{number}.dcm"
                )
                lines.append(f"  - `{name}` -- SOP `{sop_uid}`")
    lines += [
        "",
        "## De-identification",
        "",
        "Each fixture intentionally carries the identifier attributes that the ingest",
        "pipeline removes (patient name/id/birthdate/sex/address/telephone, institution",
        "and referring physician). Pixel data is never modified by de-identification.",
        "",
    ]
    return "\n".join(lines)


def write_fixtures(out_dir: Path) -> dict[str, Any]:
    """Write the fixture files and the README, returning a small manifest."""

    out_dir.mkdir(parents=True, exist_ok=True)
    files = build_fixtures()
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    (out_dir / "README.md").write_text(readme_text(), encoding="utf-8")
    return {
        "dir": str(out_dir),
        "files": sorted(files),
        "count": len(files),
    }


__all__ = [
    "FIXTURE_IDENTIFIERS",
    "FIXTURE_STUDIES",
    "IMAGE_SIZE",
    "STUDY_DATE",
    "STUDY_DATE_STR",
    "UID_ROOT",
    "build_dataset",
    "build_fixtures",
    "readme_text",
    "sop_instance_uid",
    "write_fixtures",
]
