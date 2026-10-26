"""Clearinghouse adapter protocol (T134).

An adapter turns a validated claim into a submission and answers an eligibility
probe. The v1 adapter is a deterministic mock: no socket, no X12. A real
clearinghouse is a later implementation behind the same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class SubmissionResult:
    """The outcome of submitting one claim."""

    external_ref: str
    status: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EligibilityResult:
    """The outcome of an eligibility probe."""

    status: str
    response: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""


@runtime_checkable
class ClearinghouseAdapter(Protocol):
    """The clearinghouse surface the RCM services depend on."""

    name: str

    def submit_claim(self, claim: Any) -> SubmissionResult:
        ...

    def check_eligibility(self, policy: Any, service_date: date) -> EligibilityResult:
        ...


__all__ = [
    "ClearinghouseAdapter",
    "EligibilityResult",
    "SubmissionResult",
]
