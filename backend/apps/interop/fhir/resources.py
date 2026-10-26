"""Map-and-validate entry points for the FHIR facade (T130).

Every renderer projects an internal model, validates the projection against the
published FHIR model and returns the validated dict. A projection bug therefore
surfaces as :class:`~apps.interop.fhir.validate.FhirValidationError` (a 500), not
as a silently malformed resource on the wire.
"""

from __future__ import annotations

from typing import Any

from .mapping import (
    appointment_to_fhir,
    encounter_to_fhir,
    patient_to_fhir,
)
from .validate import validate_resource

#: Media type mandated by the FHIR specification for JSON resources.
FHIR_MEDIA_TYPE = "application/fhir+json"


def render_patient(patient: Any) -> dict[str, Any]:
    return validate_resource("Patient", patient_to_fhir(patient))


def render_appointment(appointment: Any) -> dict[str, Any]:
    return validate_resource("Appointment", appointment_to_fhir(appointment))


def render_encounter(encounter: Any) -> dict[str, Any]:
    return validate_resource("Encounter", encounter_to_fhir(encounter))


__all__ = [
    "FHIR_MEDIA_TYPE",
    "render_appointment",
    "render_encounter",
    "render_patient",
]
