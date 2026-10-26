"""Claim validation rule registry (T134).

A claim is validated by running every registered rule and collecting an
*actionable* error list. Each error is ``{"code", "message", "field"}`` so the
API can return a stable code plus a human message and a field to fix.

The five baseline rules (ARCHITECTURE.md section 17 / T134):

1. ``policy_active`` -- a policy exists, is active and covers the service date.
2. ``diagnosis_codes_present`` -- at least one diagnosis code.
3. ``procedure_allowed`` -- every line's procedure code is on the allowlist.
4. ``fee_positive`` -- every line has a fee greater than zero.
5. ``tooth_required`` -- a tooth-specific procedure names a tooth.

Rules are registered with :func:`rule` so a deployment can add one without
editing the service that runs them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

#: Procedure codes a claim may carry in v1 (a deliberately small, synthetic set).
ALLOWED_PROCEDURE_CODES: frozenset[str] = frozenset(
    {
        "D0120",  # periodic oral evaluation
        "D0140",  # limited oral evaluation
        "D0150",  # comprehensive oral evaluation
        "D0274",  # bitewings
        "D0330",  # panoramic radiograph
        "D1110",  # prophylaxis
        "D2391",  # resin composite, posterior, one surface
        "D2740",  # crown, porcelain/ceramic
        "D3310",  # endodontic therapy, anterior
        "D7140",  # extraction, erupted tooth
    }
)

#: Procedure codes that must name a tooth.
TOOTH_SPECIFIC_CODES: frozenset[str] = frozenset({"D2391", "D2740", "D3310", "D7140"})

#: ICD-10-ish diagnosis code shape (letter, two digits, optional decimal).
DIAGNOSIS_CODE_RE = re.compile(r"^[A-TV-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")

ValidationRule = Callable[[Any], list[dict[str, Any]]]

RULES: list[ValidationRule] = []


def rule(func: ValidationRule) -> ValidationRule:
    """Register ``func`` as a validation rule."""

    RULES.append(func)
    return func


def _service_date(claim: Any) -> date:
    submitted = getattr(claim, "submitted_at", None)
    if submitted is not None:
        return submitted.date()
    created = getattr(claim, "created_at", None)
    if created is not None:
        return created.date()
    return date.today()


@rule
def policy_active(claim: Any) -> list[dict[str, Any]]:
    policy = getattr(claim, "policy", None)
    if policy is None:
        return [
            {
                "code": "policy_missing",
                "message": "The claim has no insurance policy attached.",
                "field": "policy",
            }
        ]
    service_date = _service_date(claim)
    errors: list[dict[str, Any]] = []
    if not policy.is_active:
        errors.append(
            {
                "code": "policy_inactive",
                "message": "The insurance policy is not active.",
                "field": "policy",
            }
        )
    if policy.valid_from and policy.valid_from > service_date:
        errors.append(
            {
                "code": "policy_not_yet_effective",
                "message": "The insurance policy is not yet effective for the service date.",
                "field": "policy",
            }
        )
    if policy.valid_to and policy.valid_to < service_date:
        errors.append(
            {
                "code": "policy_expired",
                "message": "The insurance policy expired before the service date.",
                "field": "policy",
            }
        )
    return errors


@rule
def diagnosis_codes_present(claim: Any) -> list[dict[str, Any]]:
    codes = getattr(claim, "diagnosis_codes", None) or []
    if not codes:
        return [
            {
                "code": "diagnosis_missing",
                "message": "At least one diagnosis code is required.",
                "field": "diagnosis_codes",
            }
        ]
    invalid = [str(code) for code in codes if not DIAGNOSIS_CODE_RE.match(str(code))]
    if invalid:
        return [
            {
                "code": "diagnosis_invalid",
                "message": f"Invalid diagnosis code(s): {', '.join(sorted(invalid))}.",
                "field": "diagnosis_codes",
            }
        ]
    return []


def _lines(claim: Any) -> list[Any]:
    from .models import ClaimLine

    return list(ClaimLine.all_objects.filter(claim_id=claim.pk).order_by("sequence", "id"))


@rule
def procedure_allowed(claim: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for line in _lines(claim):
        if str(line.procedure_code) not in ALLOWED_PROCEDURE_CODES:
            errors.append(
                {
                    "code": "procedure_not_allowed",
                    "message": f"Procedure code '{line.procedure_code}' is not allowed.",
                    "field": "lines",
                }
            )
    return errors


@rule
def fee_positive(claim: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for line in _lines(claim):
        if int(line.fee_minor) <= 0:
            errors.append(
                {
                    "code": "fee_not_positive",
                    "message": f"Procedure '{line.procedure_code}' has a non-positive fee.",
                    "field": "lines",
                }
            )
    return errors


@rule
def tooth_required(claim: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for line in _lines(claim):
        if (
            str(line.procedure_code) in TOOTH_SPECIFIC_CODES
            and line.tooth is None
        ):
            errors.append(
                {
                    "code": "tooth_required",
                    "message": f"Procedure '{line.procedure_code}' requires a tooth number.",
                    "field": "lines",
                }
            )
    return errors


def validate_claim(claim: Any) -> list[dict[str, Any]]:
    """Run every registered rule and return the collected error list."""

    errors: list[dict[str, Any]] = []
    for rule_func in RULES:
        errors.extend(rule_func(claim))
    return errors


__all__ = [
    "ALLOWED_PROCEDURE_CODES",
    "DIAGNOSIS_CODE_RE",
    "RULES",
    "TOOTH_SPECIFIC_CODES",
    "rule",
    "validate_claim",
]
