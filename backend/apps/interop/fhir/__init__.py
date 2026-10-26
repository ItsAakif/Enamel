"""FHIR facade for the interop app (T130).

The facade is read-only. Internal models are projected to FHIR R4-shaped dicts in
:mod:`apps.interop.fhir.mapping` and validated against the ``fhir.resources``
pydantic models in :mod:`apps.interop.fhir.validate` before a byte reaches a
client.
"""

from __future__ import annotations

from .resources import (
    FHIR_MEDIA_TYPE,
    render_appointment,
    render_encounter,
    render_patient,
)
from .validate import FhirValidationError, validate_resource

__all__ = [
    "FHIR_MEDIA_TYPE",
    "FhirValidationError",
    "render_appointment",
    "render_encounter",
    "render_patient",
    "validate_resource",
]
