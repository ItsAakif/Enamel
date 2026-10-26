"""FHIR resource validation with the ``fhir.resources`` pydantic models (T130).

Version note (documented deviation)
-----------------------------------
``fhir.resources`` publishes the R4 line as the ``R4B`` subpackage from 7.x
onwards (7.x requires pydantic 2, which this project pins). The 6.x line ships
exact R4 modules but requires pydantic v1 and therefore cannot import against
pydantic 2.14. The facade therefore validates with ``fhir.resources.R4B.*`` --
the R4 errata ("R4B") models -- which keep the R4 resource shapes (e.g. a single
``Encounter.class`` Coding) while remaining pydantic-2 compatible. This is a
real validation against the published models, not a hand-rolled schema check.

A resource that fails validation raises :class:`FhirValidationError`; the API
turns that into a 500 because it means the *server* produced a malformed
resource (never a client input).
"""

from __future__ import annotations

from typing import Any

from fhir.resources.R4B.appointment import Appointment as FhirAppointment
from fhir.resources.R4B.encounter import Encounter as FhirEncounter
from fhir.resources.R4B.patient import Patient as FhirPatient

#: Resource type -> published pydantic model.
RESOURCE_MODELS: dict[str, Any] = {
    "Patient": FhirPatient,
    "Appointment": FhirAppointment,
    "Encounter": FhirEncounter,
}

SUPPORTED_RESOURCE_TYPES: tuple[str, ...] = tuple(sorted(RESOURCE_MODELS))


class FhirValidationError(ValueError):
    """A projected resource did not validate against the FHIR model."""

    def __init__(self, resource_type: str, message: str) -> None:
        self.resource_type = resource_type
        self.message = message
        super().__init__(f"{resource_type}: {message}")


def validate_resource(resource_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate ``payload`` and return the canonical (serialisable) resource.

    The model is constructed from the payload, which runs every field/type and
    required-field check the published schema declares. ``.dict()`` returns a
    plain mapping with the validated values (fhir.resources' pydantic-v1 style
    accessor, available on both 6.x and 7.x).
    """

    model = RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise FhirValidationError(resource_type, "unsupported resource type")
    if not isinstance(payload, dict):
        raise FhirValidationError(resource_type, "resource must be an object")

    data = dict(payload)
    data["resourceType"] = resource_type
    try:
        instance = model(**data)
    except Exception as exc:  # pydantic v1/v2 ValidationError, or a coercion error
        raise FhirValidationError(resource_type, str(exc)) from exc

    try:
        return instance.dict()
    except Exception as exc:  # pragma: no cover - defensive
        raise FhirValidationError(resource_type, "resource could not be serialised") from exc


def validate_any(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a payload using its own ``resourceType`` field."""

    resource_type = str((payload or {}).get("resourceType") or "")
    return validate_resource(resource_type, payload)


__all__ = [
    "RESOURCE_MODELS",
    "SUPPORTED_RESOURCE_TYPES",
    "FhirValidationError",
    "validate_any",
    "validate_resource",
]
