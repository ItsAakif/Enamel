"""Tool-argument validity suite (T126).

Covers valid, missing, extra, wrong-type and cross-clinic arguments. Every case
asserts the registry's outcome *code*, never a message.
"""

from __future__ import annotations

import time
from typing import Any

from ..suites import load_dataset, result_row, substitute

SUITE = "tools"


def run(world: Any, provider: Any, seed: int) -> list[dict[str, Any]]:
    from apps.ai.tools.base import ToolContext
    from apps.ai.tools.registry import get_registry

    registry = get_registry()
    ctx = ToolContext(
        clinic=world.clinic_a, user=world.user_a, membership=world.membership_a
    )
    mapping = world.placeholders()
    rows: list[dict[str, Any]] = []

    for case in load_dataset(SUITE):
        args = substitute(case.get("args") or {}, mapping)
        expected = case["expect"]
        started = time.perf_counter()
        result = registry.execute(ctx, case["tool"], args)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if expected == "ok":
            passed = result.status == "ok"
        else:
            passed = result.status == "rejected" and result.error_code == expected
        reason = "" if passed else f"status={result.status} code={result.error_code}"

        rows.append(
            result_row(SUITE, case["id"], passed, reason, latency_ms=latency_ms)
        )

    return rows
