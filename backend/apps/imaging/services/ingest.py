"""Study ingest: validate, de-identify, store and index DICOM instances (T127).

The pipeline is deliberately linear and fail-closed:

1. size cap (413) -- checked before anything is written;
2. DICOM magic bytes + pydicom parse (400 ``invalid_dicom``);
3. de-identify the configured tags (pixel data is untouched);
4. SHA-256 of the de-identified bytes -> content-addressed storage key;
5. duplicate SOP Instance UID + identical hash -> idempotent no-op; a different
   hash for the same SOP UID is a 409 conflict;
6. preview render, recording ``preview_status`` (a failure is recorded, never a
   blank image);
7. audit every stored instance with the actor and the clinic.

The per-clinic daily quota is enforced before storage so a burst cannot fill the
volume; exceeding it is a 429.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from .. import storage
from ..dicom.deid import deidentify, serialize_dataset
from ..dicom.parse import InvalidDicomError, extract_metadata, parse_dicom
from ..dicom.preview import PreviewError, render_jpeg
from ..models import Instance, Series, Study

# The 50 MB default mirrors the hard per-object limit of free-tier object
# storage (Supabase Storage) and the nginx client_max_body_size of both web
# tiers (deploy/nginx.conf and deploy/render/nginx.conf.template), so an
# oversized upload is rejected by the first guard it meets instead of being
# buffered.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_DAILY_QUOTA = 500
DEFAULT_PREVIEW_MAX_PIXELS = 512


@dataclass
class UploadResult:
    """Outcome of one multipart upload batch."""

    studies: list[Study] = field(default_factory=list)
    created_instances: int = 0
    skipped_instances: int = 0
    failed_previews: int = 0


def max_bytes() -> int:
    return int(getattr(settings, "IMAGING_MAX_BYTES", DEFAULT_MAX_BYTES))


def daily_quota() -> int:
    return int(getattr(settings, "IMAGING_DAILY_INSTANCE_QUOTA", DEFAULT_DAILY_QUOTA))


def preview_max_pixels() -> int:
    return int(getattr(settings, "IMAGING_PREVIEW_MAX_PIXELS", DEFAULT_PREVIEW_MAX_PIXELS))


def _invalid_dicom(filename: str) -> ApiError:
    return ApiError(
        "invalid_dicom",
        "The uploaded file is not a valid DICOM object.",
        status_code=400,
        field_errors={filename or "file": ["not a valid DICOM object"]},
    )


def _foreign_object(kind: str) -> ApiError:
    """Return the opaque error used for an object owned by another clinic.

    A Study/Series/Instance Instance UID is looked up globally, so a UID that
    belongs to a different tenant used to answer "already exists in another
    clinic". That is a cross-tenant existence oracle: it tells the caller that
    a given identifier exists somewhere on the platform. The rest of the
    codebase already answers 404 for a foreign object, and 404 is what a
    non-existent object returns, so the two cases cannot be told apart.
    """

    return ApiError(
        "not_found",
        f"The {kind} could not be found.",
        status_code=404,
    )


def _instance_for_sop(sop_instance_uid: str) -> Instance | None:
    return Instance.all_objects.filter(sop_instance_uid=sop_instance_uid).first()


def _today_instance_count(clinic: Any) -> int:
    start = timezone.localtime(timezone.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return Instance.all_objects.filter(
        clinic_id=clinic.id, created_at__gte=start
    ).count()


def _get_or_create_study(clinic: Any, patient: Any, meta: dict[str, Any], actor: Any) -> tuple[Study, bool]:
    uid = meta["study_instance_uid"]
    existing = Study.all_objects.filter(study_instance_uid=uid).first()
    if existing is not None:
        if existing.clinic_id != clinic.id:
            raise _foreign_object("study")
        return existing, False
    study = Study.all_objects.create(
        clinic_id=clinic.id,
        patient=patient,
        study_instance_uid=uid,
        accession=meta["accession"][:64],
        modality=meta["modality"][:16],
        study_date=meta["study_date"],
        description=meta["description"][:255],
        source=Study.Source.STAFF,
        created_by=actor,
    )
    return study, True


def _get_or_create_series(clinic: Any, study: Study, meta: dict[str, Any]) -> tuple[Series, bool]:
    uid = meta["series_instance_uid"]
    existing = Series.all_objects.filter(series_instance_uid=uid).first()
    if existing is not None:
        if existing.clinic_id != clinic.id:
            raise _foreign_object("series")
        return existing, False
    series = Series.all_objects.create(
        clinic_id=clinic.id,
        study=study,
        series_instance_uid=uid,
        modality=(meta["modality"] or "")[:16],
        body_part=(meta["body_part"] or "")[:64],
        description=(meta["series_description"] or "")[:255],
    )
    return series, True


def _restore_missing_bytes(instance: Instance, data: bytes) -> None:
    """Re-materialise an idempotent re-upload whose stored bytes disappeared.

    The index row and the object store can drift when the media volume is
    replaced or restored separately from the database. A repeated upload of the
    same SOP instance is already accepted as an idempotent no-op, so it is also
    the natural point to heal the store instead of leaving a row that can never
    be previewed or analysed again.
    """

    if storage.exists(instance.storage_key):
        return

    storage.save_bytes(instance.storage_key, data)

    if instance.preview_status != Instance.PreviewStatus.READY:
        return
    if instance.preview_key and storage.exists(instance.preview_key):
        return

    try:
        dataset = parse_dicom(data)
        jpeg = render_jpeg(dataset, max_size=preview_max_pixels())
    except (InvalidDicomError, PreviewError):
        instance.preview_key = None
        instance.preview_status = Instance.PreviewStatus.FAILED
        instance.save(update_fields=["preview_key", "preview_status", "updated_at"])
        return

    preview_key = instance.preview_key or storage.storage_key(
        instance.clinic_id, instance.series.study.patient_id, instance.sha256, suffix=".jpg"
    )
    storage.save_bytes(preview_key, jpeg)
    instance.preview_key = preview_key
    instance.save(update_fields=["preview_key", "updated_at"])


def _store_instance(
    *,
    clinic: Any,
    series: Series,
    meta: dict[str, Any],
    data: bytes,
    deid: dict[str, Any],
    actor: Any,
    request: Any,
) -> Instance:
    key = storage.storage_key(clinic.id, series.study.patient_id, meta["sha256"])
    storage.save_bytes(key, data)

    preview_key = None
    preview_status = Instance.PreviewStatus.FAILED
    try:
        dataset = parse_dicom(data)
        jpeg = render_jpeg(dataset, max_size=preview_max_pixels())
    except (InvalidDicomError, PreviewError):
        preview_status = Instance.PreviewStatus.FAILED
    else:
        preview_key = storage.storage_key(
            clinic.id, series.study.patient_id, meta["sha256"], suffix=".jpg"
        )
        storage.save_bytes(preview_key, jpeg)
        preview_status = Instance.PreviewStatus.READY

    instance = Instance.all_objects.create(
        clinic_id=clinic.id,
        series=series,
        sop_instance_uid=meta["sop_instance_uid"],
        instance_number=meta["instance_number"],
        rows=meta["rows"],
        columns=meta["columns"],
        bits_allocated=meta["bits_allocated"],
        photometric=(meta["photometric"] or "")[:32],
        transfer_syntax=(meta["transfer_syntax"] or "")[:64],
        storage_key=key,
        sha256=meta["sha256"],
        size_bytes=len(data),
        deid_json=deid,
        preview_key=preview_key,
        preview_status=preview_status,
    )
    record_audit(
        "imaging.instance_uploaded",
        entity=instance,
        after={
            "sop_instance_uid": instance.sop_instance_uid,
            "series_instance_uid": series.series_instance_uid,
            "sha256": instance.sha256,
            "size_bytes": instance.size_bytes,
            "preview_status": instance.preview_status,
        },
        actor=actor,
        request=request,
    )
    return instance


def upload_studies(
    *,
    clinic: Any,
    patient: Any,
    files: Iterable[tuple[str, bytes]],
    actor: Any = None,
    request: Any = None,
) -> UploadResult:
    """Ingest a batch of DICOM files. Returns the studies touched."""

    result = UploadResult()
    seen: dict[str, Study] = {}
    limit = max_bytes()

    with with_clinic(clinic):
        for filename, data in files:
            if len(data) > limit:
                raise ApiError(
                    "payload_too_large",
                    "The uploaded file is too large.",
                    status_code=413,
                    details={"max_bytes": limit},
                )

            try:
                dataset = parse_dicom(data)
            except InvalidDicomError as exc:
                raise _invalid_dicom(filename) from exc

            meta = extract_metadata(dataset)
            if not meta["study_instance_uid"] or not meta["series_instance_uid"]:
                raise _invalid_dicom(filename)

            deid_result = deidentify(dataset)
            payload = serialize_dataset(dataset)
            meta["sha256"] = hashlib.sha256(payload).hexdigest()
            meta["deid"] = deid_result.as_dict()

            existing = _instance_for_sop(meta["sop_instance_uid"])
            if existing is not None:
                if existing.clinic_id != clinic.id:
                    # Opaque: a foreign SOP Instance UID must look exactly like
                    # an unknown one (see ``_foreign_object``).
                    raise _foreign_object("instance")
                if existing.sha256 != meta["sha256"]:
                    raise ApiError(
                        "conflict",
                        "The SOP Instance UID already exists with different content.",
                        status_code=409,
                    )
                _restore_missing_bytes(existing, payload)
                result.skipped_instances += 1
                seen.setdefault(meta["study_instance_uid"], existing.series.study)
                continue

            if _today_instance_count(clinic) >= daily_quota():
                raise ApiError(
                    "throttled",
                    "The daily imaging upload quota for this clinic has been reached.",
                    status_code=429,
                    details={"quota": daily_quota()},
                )

            study, _ = _get_or_create_study(clinic, patient, meta, actor)
            series, _ = _get_or_create_series(clinic, study, meta)
            instance = _store_instance(
                clinic=clinic,
                series=series,
                meta=meta,
                data=payload,
                deid=meta["deid"],
                actor=actor,
                request=request,
            )
            series.instance_count = Instance.all_objects.filter(series=series).count()
            series.save(update_fields=["instance_count", "updated_at"])

            if instance.preview_status == Instance.PreviewStatus.FAILED:
                result.failed_previews += 1
            result.created_instances += 1
            seen[meta["study_instance_uid"]] = study

    result.studies = list(seen.values())
    return result


__all__ = [
    "DEFAULT_DAILY_QUOTA",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_PREVIEW_MAX_PIXELS",
    "UploadResult",
    "daily_quota",
    "max_bytes",
    "preview_max_pixels",
    "upload_studies",
]
