"""Encounter service (T106).

An encounter can point at an appointment produced by the scheduling app (T110).
``Encounter.appointment_id`` is still a plain UUID -- the FK is deliberately not
introduced yet -- so the reference is validated through the app registry: when
``scheduling`` is installed the referenced appointment must exist *in the same
clinic*; when it is not installed the UUID is accepted as an opaque reference.
The behaviour is documented here and in the task progress log.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps
from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic

from ..models import Encounter, Patient


def appointment_available() -> bool:
    """True when the scheduling app (and its ``Appointment`` model) is installed."""

    return django_apps.is_installed("apps.scheduling")


def validate_appointment_reference(clinic: Any, appointment_id: Any) -> None:
    """Raise a field-addressed 400 when ``appointment_id`` is foreign or missing.

    Only called when the scheduling app is installed; otherwise the id is kept
    as an opaque reference (see the module docstring).
    """

    if appointment_id is None:
        return
    if not appointment_available():
        return
    Appointment = django_apps.get_model("scheduling", "Appointment")
    manager = getattr(Appointment, "all_objects", None)
    if manager is None:  # pragma: no cover - a real model always exposes one
        manager = Appointment._default_manager
    exists = manager.filter(
        pk=appointment_id,
        clinic_id=getattr(clinic, "pk", clinic),
    ).exists()
    if not exists:
        raise ApiError(
            "validation_error",
            "The request could not be processed.",
            status_code=400,
            field_errors={"appointment_id": ["appointment not found in this clinic"]},
        )


def create_encounter(
    patient: Patient,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> Encounter:
    """Create an encounter for ``patient`` and audit it."""

    appointment_id = fields.get("appointment_id")
    validate_appointment_reference(patient.clinic, appointment_id)

    with transaction.atomic():
        with with_clinic(patient.clinic):
            encounter = Encounter.objects.create(
                clinic=patient.clinic,
                patient=patient,
                **fields,
            )
            record_audit(
                "encounter.created",
                entity=encounter,
                after={
                    "kind": encounter.kind,
                    "status": encounter.status,
                    "occurred_at": encounter.occurred_at,
                    "appointment_id": encounter.appointment_id,
                    # Named "code_count" on purpose: a key containing
                    # "diagnosis" is redacted by core.redaction, and the count
                    # is safe metadata.
                    "code_count": len(encounter.diagnosis_codes or []),
                },
                actor=actor,
                request=request,
            )
    return encounter


__all__ = [
    "appointment_available",
    "create_encounter",
    "validate_appointment_reference",
]
