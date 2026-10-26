"""Clearinghouse adapter registry (T134).

``RCM_ADAPTER`` selects the adapter. Only ``mock`` exists in v1; the resolver
refuses an unknown value so a misconfiguration fails loudly rather than silently
falling back to a live path.
"""

from __future__ import annotations

from django.conf import settings

from .base import ClearinghouseAdapter, EligibilityResult, SubmissionResult
from .mock_clearinghouse import MockClearinghouse

ADAPTER_CHOICES = ("mock",)


def configured_adapter() -> str:
    return str(getattr(settings, "RCM_ADAPTER", "mock") or "mock").strip().lower()


def resolve_adapter() -> ClearinghouseAdapter:
    """Return the configured clearinghouse adapter."""

    configured = configured_adapter()
    if configured == "mock":
        return MockClearinghouse()
    raise ValueError(f"unknown RCM_ADAPTER '{configured}'")


__all__ = [
    "ADAPTER_CHOICES",
    "ClearinghouseAdapter",
    "EligibilityResult",
    "MockClearinghouse",
    "SubmissionResult",
    "configured_adapter",
    "resolve_adapter",
]
