"""Regression comparison for evaluation runs (T126).

A run regresses when a case that passed in the baseline now fails, or when a
suite's wall-clock duration grows past ``max_latency_ratio`` times the baseline.
Thresholds are configuration with conservative defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_NEW_FAILURES = 0
DEFAULT_MAX_LATENCY_RATIO = 3.0


@dataclass(frozen=True)
class Thresholds:
    max_new_failures: int = DEFAULT_MAX_NEW_FAILURES
    max_latency_ratio: float = DEFAULT_MAX_LATENCY_RATIO


def _status_map(report: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {
        (suite["suite"], case["case"]): case["status"]
        for suite in report["suites"]
        for case in suite["cases"]
    }


def _suite_latency(report: dict[str, Any]) -> dict[str, int]:
    return {suite["suite"]: int(suite["duration_ms"]) for suite in report["suites"]}


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any],
    thresholds: Thresholds | None = None,
) -> list[str]:
    """Return a list of regression descriptions (empty means no regression)."""

    limits = thresholds or Thresholds()
    regressions: list[str] = []

    base_status = _status_map(baseline)
    cur_status = _status_map(current)
    new_failures = [
        key
        for key, status in base_status.items()
        if status == "pass" and cur_status.get(key, "fail") == "fail"
    ]
    if len(new_failures) > limits.max_new_failures:
        regressions.extend(
            f"case regressed: {suite}/{case}" for suite, case in sorted(new_failures)
        )

    base_latency = _suite_latency(baseline)
    for suite, current_ms in _suite_latency(current).items():
        baseline_ms = base_latency.get(suite)
        if baseline_ms and baseline_ms > 0 and current_ms > baseline_ms * limits.max_latency_ratio:
            regressions.append(
                f"latency regressed: {suite} {current_ms}ms > "
                f"{baseline_ms}ms x{limits.max_latency_ratio}"
            )

    return regressions


__all__ = ["DEFAULT_MAX_LATENCY_RATIO", "DEFAULT_MAX_NEW_FAILURES", "Thresholds", "compare"]
