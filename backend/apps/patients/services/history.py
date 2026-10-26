"""Allergy and medical-history services (T106).

Read paths return scoped querysets; write paths create/update the row and its
audit entry in one transaction. The summary helpers are pure functions over
already-loaded rows so the patient detail view can render severity/active
counts without extra queries.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.redaction import changed_field_names
from apps.core.tenancy import with_clinic

from ..models import Allergy, MedicalHistory, Patient

SEVERITY_RANK: dict[str, int] = {
    Allergy.Severity.MILD: 1,
    Allergy.Severity.MODERATE: 2,
    Allergy.Severity.SEVERE: 3,
    Allergy.Severity.ANAPHYLAXIS: 4,
}
SEVERITY_ORDER = (
    Allergy.Severity.ANAPHYLAXIS,
    Allergy.Severity.SEVERE,
    Allergy.Severity.MODERATE,
    Allergy.Severity.MILD,
)
HISTORY_KINDS = (
    MedicalHistory.Kind.CONDITION,
    MedicalHistory.Kind.SURGERY,
    MedicalHistory.Kind.MEDICATION,
)

_ALLERGY_AUDIT_SAFE = frozenset({"severity", "is_active"})
_HISTORY_AUDIT_SAFE = frozenset({"kind", "is_active"})


def patient_allergies(patient: Patient, *, active_only: bool = False) -> list[Allergy]:
    """Return the patient's allergies, most severe and most recent first."""

    with with_clinic(patient.clinic):
        queryset = Allergy.objects.filter(patient=patient)
        if active_only:
            queryset = queryset.filter(is_active=True)
        rows = list(queryset)
    rows.sort(
        key=lambda row: (
            -SEVERITY_RANK.get(row.severity, 0),
            -row.recorded_at.timestamp(),
            str(row.id),
        )
    )
    return rows


def patient_medical_history(patient: Patient, *, active_only: bool = False) -> list[MedicalHistory]:
    with with_clinic(patient.clinic):
        queryset = MedicalHistory.objects.filter(patient=patient)
        if active_only:
            queryset = queryset.filter(is_active=True)
        return list(queryset.order_by("kind", "label", "id"))


def summarize_allergies(allergies: list[Allergy]) -> dict[str, Any]:
    """Counts by severity plus the highest active severity."""

    severities = {severity: 0 for severity in SEVERITY_RANK}
    active = 0
    highest: str | None = None
    for allergy in allergies:
        severities[allergy.severity] = severities.get(allergy.severity, 0) + 1
        if allergy.is_active:
            active += 1
            if highest is None or SEVERITY_RANK.get(allergy.severity, 0) > SEVERITY_RANK.get(
                highest, 0
            ):
                highest = allergy.severity
    return {
        "total": len(allergies),
        "active_count": active,
        "highest_severity": highest,
        "severities": severities,
    }


def summarize_medical_history(entries: list[MedicalHistory]) -> dict[str, Any]:
    counts = {kind: 0 for kind in HISTORY_KINDS}
    active = 0
    for entry in entries:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
        if entry.is_active:
            active += 1
    return {
        "total": len(entries),
        "active_count": active,
        "by_kind": counts,
    }


def _allergy_audit(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict, dict]:
    changed = changed_field_names(before, after)
    payload_before = {k: v for k, v in before.items() if k in _ALLERGY_AUDIT_SAFE}
    payload_after = {k: v for k, v in after.items() if k in _ALLERGY_AUDIT_SAFE}
    payload_before["changed_fields"] = list(changed)
    payload_after["changed_fields"] = list(changed)
    return payload_before, payload_after


def _history_audit(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict, dict]:
    changed = changed_field_names(before, after)
    payload_before = {k: v for k, v in before.items() if k in _HISTORY_AUDIT_SAFE}
    payload_after = {k: v for k, v in after.items() if k in _HISTORY_AUDIT_SAFE}
    payload_before["changed_fields"] = list(changed)
    payload_after["changed_fields"] = list(changed)
    return payload_before, payload_after


def record_allergy(
    patient: Patient,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> Allergy:
    with transaction.atomic():
        with with_clinic(patient.clinic):
            allergy = Allergy.objects.create(
                clinic=patient.clinic,
                patient=patient,
                recorded_by=actor,
                **fields,
            )
            record_audit(
                "allergy.created",
                entity=allergy,
                after={
                    "severity": allergy.severity,
                    "is_active": allergy.is_active,
                    "changed_fields": sorted(fields),
                },
                actor=actor,
                request=request,
            )
    return allergy


def update_allergy(
    allergy: Allergy,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> Allergy:
    with transaction.atomic():
        with with_clinic(allergy.clinic):
            before = {name: getattr(allergy, name) for name in fields}
            for name, value in fields.items():
                setattr(allergy, name, value)
            allergy.save(update_fields=[*fields, "updated_at"])
            after = {name: getattr(allergy, name) for name in fields}
            payload_before, payload_after = _allergy_audit(before, after)
            record_audit(
                "allergy.updated",
                entity=allergy,
                before=payload_before,
                after=payload_after,
                actor=actor,
                request=request,
            )
    return allergy


def record_medical_history(
    patient: Patient,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> MedicalHistory:
    with transaction.atomic():
        with with_clinic(patient.clinic):
            entry = MedicalHistory.objects.create(
                clinic=patient.clinic,
                patient=patient,
                **fields,
            )
            record_audit(
                "medical_history.created",
                entity=entry,
                after={
                    "kind": entry.kind,
                    "is_active": entry.is_active,
                    "changed_fields": sorted(fields),
                },
                actor=actor,
                request=request,
            )
    return entry


def update_medical_history(
    entry: MedicalHistory,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> MedicalHistory:
    with transaction.atomic():
        with with_clinic(entry.clinic):
            before = {name: getattr(entry, name) for name in fields}
            for name, value in fields.items():
                setattr(entry, name, value)
            entry.save(update_fields=[*fields, "updated_at"])
            after = {name: getattr(entry, name) for name in fields}
            payload_before, payload_after = _history_audit(before, after)
            record_audit(
                "medical_history.updated",
                entity=entry,
                before=payload_before,
                after=payload_after,
                actor=actor,
                request=request,
            )
    return entry


__all__ = [
    "HISTORY_KINDS",
    "SEVERITY_ORDER",
    "SEVERITY_RANK",
    "patient_allergies",
    "patient_medical_history",
    "record_allergy",
    "record_medical_history",
    "summarize_allergies",
    "summarize_medical_history",
    "update_allergy",
    "update_medical_history",
]
