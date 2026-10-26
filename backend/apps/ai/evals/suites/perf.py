"""Performance suite (T126): latency ceiling plus token/cost recording.

Latency is recorded and only asserted against a generous ceiling so the suite is
stable on slow machines. Token and cost figures are recorded for trend analysis.
"""

from __future__ import annotations

import time
from typing import Any

from ..suites import load_dataset, result_row

SUITE = "perf"


def run(world: Any, provider: Any, seed: int) -> list[dict[str, Any]]:
    from apps.ai.providers.base import Message
    from apps.ai.rag.answer import answer_question
    from apps.ai.rag.retrieve import search
    from apps.ai.services.cost import compute_cost_minor
    from apps.ai.services.invoke import invoke

    rows: list[dict[str, Any]] = []

    for case in load_dataset(SUITE):
        ceiling = int(case["ceiling_ms"])
        tokens = 0
        cost_minor = 0
        started = time.perf_counter()
        try:
            if case["case"] == "chat":
                result = invoke(
                    purpose="eval",
                    messages=[Message(role="user", content="hello")],
                    clinic=world.clinic_a,
                    user=world.user_a,
                    provider=provider,
                )
                tokens = result.usage.total_tokens
                cost_minor = compute_cost_minor(
                    result.model, result.usage.prompt_tokens, result.usage.completion_tokens
                )
                passed = tokens > 0 and cost_minor >= 0
            elif case["case"] == "retrieval":
                results = search(world.clinic_a, "opening hours", top_k=5, provider=provider)
                tokens = sum(item.token_count for item in results)
                passed = bool(results) and tokens > 0
            else:  # answer
                answer = answer_question(world.clinic_a, "opening hours", provider=provider)
                tokens = answer.usage.total_tokens
                cost_minor = compute_cost_minor(
                    answer.model,
                    answer.usage.prompt_tokens,
                    answer.usage.completion_tokens,
                )
                passed = tokens > 0
            latency_ms = int((time.perf_counter() - started) * 1000)
            passed = passed and latency_ms < ceiling
            reason = f"latency={latency_ms}ms ceiling={ceiling}ms tokens={tokens} cost={cost_minor}"
        except Exception as exc:  # noqa: BLE001 - a case failure, not a crash
            latency_ms = int((time.perf_counter() - started) * 1000)
            passed, reason = False, f"exception={type(exc).__name__}"

        rows.append(
            result_row(
                SUITE,
                case["id"],
                passed,
                reason,
                latency_ms=latency_ms,
                tokens=tokens,
                cost_minor=cost_minor,
            )
        )

    return rows
