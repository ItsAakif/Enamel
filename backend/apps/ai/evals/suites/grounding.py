"""Groundedness suite (T126): grounded answers vs fixed-string refusal."""

from __future__ import annotations

import time
from typing import Any

from ..suites import load_dataset, result_row

SUITE = "grounding"


def run(world: Any, provider: Any, seed: int) -> list[dict[str, Any]]:
    from apps.ai.rag.answer import REFUSAL_TEXT, answer_question

    rows: list[dict[str, Any]] = []

    for case in load_dataset(SUITE):
        started = time.perf_counter()
        tokens = 0
        try:
            answer = answer_question(
                world.clinic_a,
                case["query"],
                provider=provider,
                min_score=case.get("min_score"),
            )
            tokens = answer.usage.total_tokens
            if case["expect"] == "grounded":
                passed = (not answer.refused) and bool(answer.citations)
                reason = f"refused={answer.refused} citations={len(answer.citations)}"
            else:
                fixed = answer.answer == REFUSAL_TEXT
                passed = answer.refused and fixed and not answer.citations
                reason = f"refused={answer.refused} fixed_string={fixed}"
        except Exception as exc:  # noqa: BLE001 - a case failure, not a crash
            passed, reason = False, f"exception={type(exc).__name__}"

        latency_ms = int((time.perf_counter() - started) * 1000)
        rows.append(
            result_row(
                SUITE, case["id"], passed, reason, latency_ms=latency_ms, tokens=tokens
            )
        )

    return rows
