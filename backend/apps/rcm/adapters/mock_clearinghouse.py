"""Deterministic mock clearinghouse (T134).

Every response is a pure function of its input, so a test can assert an exact
external reference and an exact eligibility payload. The adapter never opens a
socket; the reference is derived from a hash of the claim id, not a random id.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from .base import EligibilityResult, SubmissionResult


def _digest(value: Any, length: int = 12) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length].upper()


class MockClearinghouse:
    """A deterministic, offline clearinghouse."""

    name = "mock"

    def submit_claim(self, claim: Any) -> SubmissionResult:
        external_ref = f"MOCK-{_digest(claim.pk)}"
        return SubmissionResult(
            external_ref=external_ref,
            status="submitted",
            payload={
                "external_ref": external_ref,
                "payer_code": getattr(getattr(claim, "payer", None), "code", ""),
                "total_minor": int(getattr(claim, "total_minor", 0)),
                "line_count": len(list(claim.lines.all())) if hasattr(claim, "lines") else 0,
            },
        )

    def check_eligibility(self, policy: Any, service_date: date) -> EligibilityResult:
        if policy is None:
            return EligibilityResult(
                status="error",
                response={"reason": "policy_missing"},
                error_code="policy_missing",
            )
        payer_code = getattr(getattr(policy, "payer", None), "code", "")
        response = {
            "payer_code": payer_code,
            "member_id_suffix": str(getattr(policy, "member_id", ""))[-4:],
            "plan_name": getattr(policy, "plan_name", ""),
            "service_date": service_date.isoformat(),
        }
        if not getattr(policy, "is_active", False):
            response["reason"] = "policy_inactive"
            return EligibilityResult("ineligible", response, "policy_inactive")
        if policy.valid_from and policy.valid_from > service_date:
            response["reason"] = "policy_not_yet_effective"
            return EligibilityResult("ineligible", response, "policy_not_yet_effective")
        if policy.valid_to and policy.valid_to < service_date:
            response["reason"] = "policy_expired"
            return EligibilityResult("ineligible", response, "policy_expired")
        response["coverage_percent"] = 80
        response["deductible_minor"] = 0
        return EligibilityResult("eligible", response, "")


__all__ = ["MockClearinghouse"]
