"""Patient create / update / archive services.

The write path is a service so the same code backs the HTTP views, future AI
tools and the retention command. Every mutation and its audit row share one
transaction: a failed audit rolls the change back (T103 constraint).

Audit payloads are deliberately narrow. Free-text demographics (name, email,
phone, address, notes, gender identity) are never copied into an audit row --
only the changed *field names* plus enum/id values are stored, on top of the
``core.redaction`` guard.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.redaction import changed_field_names
from apps.core.tenancy import with_clinic

from ..models import Patient
from .numbering import allocate_patient_number

# Fields whose values may appear in an audit row. Everything else (names,
# email, phone, address, notes, gender identity) is reported by name only.
AUDIT_SAFE_FIELDS = frozenset(
    {
        "number",
        "status",
        "source",
        "preferred_contact",
        "language",
        "sex_at_birth",
        "primary_location",
        "primary_location_id",
        "primary_provider",
        "primary_provider_id",
        "merged_into",
        "merged_into_id",
        "deleted_at",
    }
)


def _value(value: Any) -> Any:
    """Normalize a model value for storage in the audit ``before``/``after``."""

    pk = getattr(value, "pk", None)
    if pk is not None and not isinstance(value, (str, int)):
        return str(pk)
    return value


def patient_snapshot(patient: Patient, fields: Any) -> dict[str, Any]:
    """Return ``{field: value}`` for ``fields`` (model attribute names)."""

    return {field: _value(getattr(patient, field, None)) for field in fields}


def audit_payload(snapshot: dict[str, Any], *, changed: list[str]) -> dict[str, Any]:
    """Filter a snapshot down to non-PHI values and add the changed names."""

    payload = {
        name: value for name, value in snapshot.items() if name in AUDIT_SAFE_FIELDS
    }
    payload["changed_fields"] = list(changed)
    return payload


def create_patient(
    clinic: Any,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> Patient:
    """Allocate a number and create the patient inside one transaction."""

    with transaction.atomic():
        with with_clinic(clinic):
            number = allocate_patient_number(clinic)
            patient = Patient.objects.create(clinic=clinic, number=number, **fields)
            record_audit(
                "patient.created",
                entity=patient,
                after=audit_payload(
                    patient_snapshot(
                        patient,
                        (
                            "number",
                            "status",
                            "source",
                            "preferred_contact",
                            "language",
                            "sex_at_birth",
                        ),
                    ),
                    changed=["number", *fields],
                ),
                actor=actor,
                request=request,
            )
    return patient


def update_patient(
    patient: Patient,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> Patient:
    """Apply ``fields`` to ``patient`` and audit the change."""

    with transaction.atomic():
        with with_clinic(patient.clinic):
            before = patient_snapshot(patient, fields)
            for name, value in fields.items():
                setattr(patient, name, value)
            patient.save(update_fields=[*fields, "updated_at"])
            after = patient_snapshot(patient, fields)
            changed = changed_field_names(before, after)
            record_audit(
                "patient.updated",
                entity=patient,
                before=audit_payload(before, changed=changed),
                after=audit_payload(after, changed=changed),
                actor=actor,
                request=request,
            )
    return patient


def archive_patient(
    patient: Patient,
    *,
    actor: Any = None,
    request: Any = None,
) -> Patient:
    """Soft delete: stamp ``deleted_at`` and move the record to ``archived``."""

    from django.utils import timezone

    with transaction.atomic():
        with with_clinic(patient.clinic):
            before = {"status": patient.status, "deleted_at": patient.deleted_at}
            patient.deleted_at = timezone.now()
            patient.status = Patient.Status.ARCHIVED
            patient.save(update_fields=["deleted_at", "status", "updated_at"])
            record_audit(
                "patient.archived",
                entity=patient,
                before=before,
                after={"status": patient.status, "deleted_at": patient.deleted_at},
                actor=actor,
                request=request,
            )
    return patient


__all__ = [
    "AUDIT_SAFE_FIELDS",
    "archive_patient",
    "audit_payload",
    "create_patient",
    "patient_snapshot",
    "update_patient",
]
