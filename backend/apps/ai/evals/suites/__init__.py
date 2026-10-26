"""Evaluation suites (T126).

Every suite exposes ``run(world, provider, seed) -> list[dict]`` returning one
result row per case. Rows are ordered exactly as the dataset defines them so the
report is reproducible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"


def load_dataset(name: str) -> list[dict[str, Any]]:
    path = DATASETS_DIR / f"{name}.jsonl"
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def result_row(
    suite: str,
    case: str,
    passed: bool,
    reason: str = "",
    *,
    latency_ms: int = 0,
    tokens: int = 0,
    cost_minor: int = 0,
) -> dict[str, Any]:
    return {
        "suite": suite,
        "case": case,
        "status": "pass" if passed else "fail",
        "reason": reason,
        "latency_ms": int(latency_ms),
        "tokens": int(tokens),
        "cost_minor": int(cost_minor),
    }


def substitute(value: Any, mapping: dict[str, str]) -> Any:
    """Recursively replace ``{{key}}`` placeholders with world values."""

    if isinstance(value, str):
        rendered = value
        for key, replacement in mapping.items():
            rendered = rendered.replace("{{" + key + "}}", str(replacement))
        return rendered
    if isinstance(value, dict):
        return {key: substitute(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, mapping) for item in value]
    return value


__all__ = ["DATASETS_DIR", "load_dataset", "result_row", "substitute"]
