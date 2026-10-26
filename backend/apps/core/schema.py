"""Shared OpenAPI parameter definitions and the project schema class.

``CLINIC_ID_PARAMETER`` documents the ``X-Clinic-Id`` header that scopes a
request to one clinic. Path parameters (``clinic_id``) are derived from the URL
pattern by drf-spectacular, so only the header needs an explicit declaration.

``EnamelAutoSchema`` applies the project pagination envelope to GET list
operations only: DRF pagination never applies to create responses, and at least
one create action (``POST /imaging/studies/``) returns a bare array, so wrapping
non-GET list-shaped responses would misdescribe the runtime contract.

``EnamelAutoSchema`` also publishes the error contract. Every operation
documents the standard error statuses and every one of them references the
``ErrorEnvelope`` component, so a generated client can type an error response
instead of guessing. The ``CodeEnum``/``ErrorDetail``/``ErrorEnvelope``
components are derived from :data:`apps.core.errors.ERROR_CODES` in
:func:`postprocess_error_contract` - the error-code list is never duplicated
here or in the settings.

``EnamelAutoSchema.get_paginated_name`` removes the ``Paginated*List*`` stutter
(``PaginatedPatientListList`` -> ``PaginatedPatientList``) so the generated
clients carry a clean public identifier.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter

CLINIC_ID_PARAMETER = OpenApiParameter(
    name="X-Clinic-Id",
    type=str,
    location=OpenApiParameter.HEADER,
    description=(
        "Active clinic scope. Required when the signed-in user belongs to more "
        "than one clinic; a foreign or suspended clinic is reported as 404/403 "
        "without revealing whether it exists."
    ),
)

#: Reference to the error envelope used by every documented error response.
ERROR_ENVELOPE_REF: dict[str, str] = {"$ref": "#/components/schemas/ErrorEnvelope"}

#: The error statuses every operation documents. The runtime raises the same
#: statuses through ``apps.core.errors.api_exception_handler``; the codes are
#: enumerated by the ``CodeEnum`` component.
STANDARD_ERROR_RESPONSES: dict[str, str] = {
    "400": "The request could not be processed.",
    "401": "Authentication is required.",
    "403": "You do not have permission to perform this action.",
    "404": "Not found.",
    "409": "The request conflicts with the current state.",
    "422": "The request could not be processed.",
    "429": "Too many requests.",
    "500": "An internal error occurred.",
}


class EnamelAutoSchema(AutoSchema):
    """AutoSchema that publishes the pagination and error contracts."""

    def _get_paginator(self):  # type: ignore[override]
        if self.method != "GET":
            return None
        return super()._get_paginator()

    def get_paginated_name(self, serializer_name: str) -> str:
        """Name the pagination envelope without the ``ListList`` stutter.

        drf-spectacular composes ``Paginated{serializer_name}List``. A
        serializer that is already called ``...List`` (``PatientListSerializer``)
        therefore produced ``PaginatedPatientListList``. The trailing ``List``
        of the serializer name is dropped so the family reads
        ``PaginatedPatientList`` / ``PaginatedAllergyList``.
        """

        if serializer_name.endswith("List") and len(serializer_name) > len("List"):
            serializer_name = serializer_name[: -len("List")]
        return f"Paginated{serializer_name}List"

    def get_operation(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Attach the standard error responses to every operation."""

        operation = super().get_operation(*args, **kwargs)
        if not operation:
            return operation

        responses = operation.setdefault("responses", {})
        for status_code, description in STANDARD_ERROR_RESPONSES.items():
            responses.setdefault(
                status_code,
                {
                    "description": description,
                    "content": {"application/json": {"schema": dict(ERROR_ENVELOPE_REF)}},
                },
            )
        return operation


def postprocess_error_contract(
    result: dict[str, Any],
    generator: Any = None,
    request: Any = None,
    public: bool = True,
) -> dict[str, Any]:
    """Declare the error components, derived from the runtime code list.

    The components are added after the document is built so the code list can
    be read from :mod:`apps.core.errors` (which imports Django and DRF) without
    the settings module having to import the application at settings-load time.
    """

    from apps.core.errors import ERROR_CODES

    components = result.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    schemas["CodeEnum"] = {
        "type": "string",
        "description": "Stable machine-readable error code.",
        "enum": list(ERROR_CODES),
    }
    schemas["ErrorDetail"] = {
        "type": "object",
        "required": ["code", "message", "field_errors", "request_id", "details"],
        "properties": {
            "code": {"$ref": "#/components/schemas/CodeEnum"},
            "message": {"type": "string"},
            "field_errors": {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "request_id": {"type": "string"},
            "details": {"type": "object", "additionalProperties": {}},
        },
    }
    schemas["ErrorEnvelope"] = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {"$ref": "#/components/schemas/ErrorDetail"},
        },
    }
    return result


__all__ = [
    "CLINIC_ID_PARAMETER",
    "ERROR_ENVELOPE_REF",
    "EnamelAutoSchema",
    "STANDARD_ERROR_RESPONSES",
    "postprocess_error_contract",
]
