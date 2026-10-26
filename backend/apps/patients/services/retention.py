"""Clinical retention rules (T106).

Default policy: keep a patient's clinical record for 8 years after the last
visit, then it becomes eligible for purge. ``clinic.retention_json`` overrides
the window per clinic::

    {"clinical_years": 8}          # default
    {"clinical_years": 12}         # longer
    {"clinical_retention_days": 90}  # explicit day override (tests / pilots)

Dry run is the default everywhere: :func:`retention_candidates` only *reports*
what would be purged. :func:`purge_patient` is the destructive path and is only
called by the ``apply_patient_retention --apply`` command, which records a
``patient.purged`` audit row per patient inside the same transaction.

Audit rows are never deleted: the purge only removes the clinical rows.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import Patient

DEFAULT_CLINICAL_YEARS = 8
_MIN_YEAR = 1
_MAX_YEARS = 100


def retention_config(clinic: Any) -> dict[str, Any]:
    """Return the effective retention policy for ``clinic``."""

    raw = dict(getattr(clinic, "retention_json", None) or {})
    days = raw.get("clinical_retention_days")
    if isinstance(days, int) and days > 0:
        return {"clinical_days": days, "source": "retention_json"}
    years = raw.get("clinical_years")
    if not isinstance(years, int) or not (_MIN_YEAR <= years <= _MAX_YEARS):
        years = DEFAULT_CLINICAL_YEARS
    return {"clinical_years": years, "source": "retention_json" if raw.get("clinical_years") else "default"}


def retention_window(clinic: Any) -> timedelta:
    config = retention_config(clinic)
    if "clinical_days" in config:
        return timedelta(days=config["clinical_days"])
    return timedelta(days=365 * config["clinical_years"])


def last_activity_at(patient: Patient, last_encounter: Any = None) -> Any:
    """Return the timestamp of the patient's last visit (or last edit)."""

    if last_encounter is not None:
        return last_encounter
    return patient.updated_at


def retention_candidates(
    clinic: Any,
    *,
    now: Any = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return patients whose clinical record is past its retention window.

    Read-only: nothing is deleted and no audit row is written.
    """

    reference = now or timezone.now()
    cutoff = reference - retention_window(clinic)
    with with_clinic(clinic):
        queryset = Patient.objects.filter(deleted_at__isnull=True).annotate(
            last_encounter=Max("encounters__occurred_at")
        )
        if limit is not None:
            queryset = queryset[:limit]
        rows = list(queryset)

    candidates = []
    for patient in rows:
        last_activity = last_activity_at(patient, patient.last_encounter)
        if last_activity is None or last_activity > cutoff:
            continue
        candidates.append(
            {
                "id": str(patient.id),
                "number": patient.number,
                "full_name": patient.full_name,
                "status": patient.status,
                "last_activity_at": last_activity.isoformat(),
            }
        )
    candidates.sort(key=lambda row: (row["last_activity_at"], row["number"]))
    return candidates


def purge_patient(
    patient: Patient,
    *,
    actor: Any = None,
    request: Any = None,
) -> None:
    """Delete one patient's clinical rows and record a ``patient.purged`` audit."""

    with transaction.atomic():
        with with_clinic(patient.clinic):
            entity_id = str(patient.pk)
            record_audit(
                "patient.purged",
                entity=patient,
                entity_id=entity_id,
                after={
                    "number": patient.number,
                    "status": patient.status,
                    "retention_policy": retention_config(patient.clinic),
                },
                actor=actor,
                request=request,
            )
            patient.delete()


def apply_retention(
    clinic: Any,
    *,
    dry_run: bool = True,
    actor: Any = None,
    request: Any = None,
    now: Any = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Report (and optionally purge) retention-eligible patients."""

    candidates = retention_candidates(clinic, now=now, limit=limit)
    result: dict[str, Any] = {
        "clinic_id": str(getattr(clinic, "pk", clinic)),
        "dry_run": dry_run,
        "retention": retention_config(clinic),
        "candidates": candidates,
        "purged": 0,
    }
    if dry_run:
        return result

    purged = 0
    for candidate in candidates:
        with with_clinic(clinic):
            patient = Patient.objects.filter(pk=candidate["id"]).first()
        if patient is None:
            continue
        purge_patient(patient, actor=actor, request=request)
        purged += 1
    result["purged"] = purged
    return result


__all__ = [
    "DEFAULT_CLINICAL_YEARS",
    "apply_retention",
    "last_activity_at",
    "purge_patient",
    "retention_candidates",
    "retention_config",
    "retention_window",
]
