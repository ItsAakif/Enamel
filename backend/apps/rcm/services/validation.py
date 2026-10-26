"""Claim validation service (T134).

Thin wrapper over :mod:`apps.rcm.rules` that groups the rule errors into an API
field-error map and raises an actionable 400 when a claim is not submittable.
"""

from __future__ import annotations

from typing import Any

from apps.core.errors import ApiError

from .. import rules


def validate(claim: Any) -> list[dict[str, Any]]:
    """Return the actionable error list for ``claim`` (empty when valid)."""

    return rules.validate_claim(claim)


def field_errors(errors: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Group rule errors into ``{field: [messages]}``."""

    grouped: dict[str, list[str]] = {}
    for error in errors:
        key = str(error.get("field") or "claim")
        grouped.setdefault(key, []).append(str(error.get("message") or error.get("code")))
    return grouped


def assert_submittable(claim: Any) -> None:
    """Raise a 400 with the error list when ``claim`` fails validation."""

    errors = validate(claim)
    if errors:
        raise ApiError(
            "validation_error",
            "The claim is not ready to submit.",
            status_code=400,
            field_errors=field_errors(errors),
            details={"errors": errors},
        )


__all__ = ["assert_submittable", "field_errors", "validate"]
