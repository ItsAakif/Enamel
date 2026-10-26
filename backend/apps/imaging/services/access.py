"""Imaging access checks (T127).

Staff access runs through the tenant manager plus an explicit clinic filter, so
a foreign study/series/instance is an indistinguishable 404. Portal (patient)
access is a separate, narrower check: a patient may only reach a study that
belongs to them. :func:`study_for_patient` / :func:`instance_for_patient` return
404 for anything else, which is the contract the portal preview endpoint will
call once the portal principal lands (T121).

Keeping the ownership rule in one module means the staff path and the future
portal path cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from apps.core.errors import ApiError

from .. import storage
from ..models import Instance, Series, Study


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def study_for_clinic(clinic: Any, study_id: Any) -> Study:
    """Return a study in ``clinic`` or raise 404."""

    study = Study.all_objects.filter(clinic_id=clinic.id, pk=study_id).first()
    if study is None:
        raise _not_found()
    return study


def series_for_clinic(clinic: Any, series_id: Any) -> Series:
    series = Series.all_objects.filter(clinic_id=clinic.id, pk=series_id).first()
    if series is None:
        raise _not_found()
    return series


def instance_for_clinic(clinic: Any, instance_id: Any) -> Instance:
    instance = Instance.all_objects.filter(clinic_id=clinic.id, pk=instance_id).first()
    if instance is None:
        raise _not_found()
    return instance


def instance_for_sop_uid(clinic: Any, sop_instance_uid: str) -> Instance:
    instance = Instance.all_objects.filter(
        clinic_id=clinic.id, sop_instance_uid=sop_instance_uid
    ).first()
    if instance is None:
        raise _not_found()
    return instance


def study_for_patient(patient: Any, study_id: Any) -> Study:
    """Return ``study_id`` only when it belongs to ``patient`` (else 404)."""

    if patient is None:
        raise _not_found()
    study = Study.all_objects.filter(
        clinic_id=patient.clinic_id, pk=study_id, patient_id=patient.id
    ).first()
    if study is None:
        raise _not_found()
    return study


def instance_for_patient(patient: Any, instance_id: Any) -> Instance:
    """Return ``instance_id`` only when its study belongs to ``patient``."""

    if patient is None:
        raise _not_found()
    instance = (
        Instance.all_objects.filter(
            clinic_id=patient.clinic_id,
            pk=instance_id,
            series__study__patient_id=patient.id,
        )
        .select_related("series__study")
        .first()
    )
    if instance is None:
        raise _not_found()
    return instance


def preview_bytes(instance: Instance) -> bytes:
    """Return the stored preview JPEG for ``instance`` or raise 404.

    A failed (or missing) preview is reported as 404 with the recorded status:
    the API never returns an empty body in place of an image.
    """

    if instance.preview_status != Instance.PreviewStatus.READY or not instance.preview_key:
        raise ApiError(
            "not_found",
            "No preview is available for this instance.",
            status_code=404,
            details={"preview_status": instance.preview_status},
        )
    if not storage.exists(instance.preview_key):
        raise ApiError(
            "not_found",
            "No preview is available for this instance.",
            status_code=404,
            details={"preview_status": instance.preview_status},
        )
    return storage.read_bytes(instance.preview_key)


__all__ = [
    "instance_for_clinic",
    "instance_for_patient",
    "instance_for_sop_uid",
    "preview_bytes",
    "series_for_clinic",
    "study_for_clinic",
    "study_for_patient",
]
