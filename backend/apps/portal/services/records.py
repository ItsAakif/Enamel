"""Portal record summary (T121).

The summary is built from an **explicit allowlist**: only the fields named below
are ever projected. Internal notes (``Patient.notes``, ``MedicalHistory.notes``,
``Encounter.notes``), staff-only references (``recorded_by``, ``provider``,
``primary_provider``) and derived clinical codes (``diagnosis_codes``) are never
included, so adding a field to a model cannot silently widen the portal payload.
"""

from __future__ import annotations

from typing import Any

from apps.patients.models import Allergy, Encounter, MedicalHistory, Patient

PATIENT_FIELDS = (
    "id",
    "first_name",
    "last_name",
    "preferred_name",
    "date_of_birth",
    "language",
    "preferred_contact",
    "phone_e164",
    "email",
)

ALLERGY_FIELDS = ("id", "substance", "severity", "is_active")

MEDICAL_HISTORY_FIELDS = ("id", "kind", "label", "since", "is_active")

ENCOUNTER_FIELDS = ("id", "kind", "occurred_at", "status", "chief_complaint")

#: Every key that may appear in a portal record summary. Used by the tests to
#: assert the allowlist stays closed.
SUMMARY_KEYS = frozenset(
    {"patient", "allergies", "medical_history", "encounters", "counts"}
)


def _project(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(obj, name) for name in fields}


def patient_summary(patient: Patient) -> dict[str, Any]:
    return _project(patient, PATIENT_FIELDS)


def record_summary(patient: Patient) -> dict[str, Any]:
    """Return the patient's own record summary (allowlisted fields only)."""

    allergies = list(
        Allergy.all_objects.filter(clinic_id=patient.clinic_id, patient=patient).order_by(
            "-recorded_at", "id"
        )
    )
    history = list(
        MedicalHistory.all_objects.filter(clinic_id=patient.clinic_id, patient=patient).order_by(
            "kind", "label", "id"
        )
    )
    encounters = list(
        Encounter.all_objects.filter(clinic_id=patient.clinic_id, patient=patient).order_by(
            "-occurred_at", "id"
        )
    )

    return {
        "patient": patient_summary(patient),
        "allergies": [_project(item, ALLERGY_FIELDS) for item in allergies],
        "medical_history": [_project(item, MEDICAL_HISTORY_FIELDS) for item in history],
        "encounters": [_project(item, ENCOUNTER_FIELDS) for item in encounters],
        "counts": {
            "allergies": len(allergies),
            "medical_history": len(history),
            "encounters": len(encounters),
        },
    }


__all__ = [
    "ALLERGY_FIELDS",
    "ENCOUNTER_FIELDS",
    "MEDICAL_HISTORY_FIELDS",
    "PATIENT_FIELDS",
    "SUMMARY_KEYS",
    "patient_summary",
    "record_summary",
]
