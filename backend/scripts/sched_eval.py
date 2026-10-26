"""Deterministic scheduling evaluation scenarios (T116).

Usage::

    python scripts/sched_eval.py --scenario cancel-fill --days 30 --seed 42

Prints a JSON summary with utilisation, conflicts and waiting time for the
waitlist scenario plus a baseline (no-waitlist) comparison and metric
definitions. The output is reproducible for a given ``(scenario, days, seed)``
because the simulation is seeded and anchored to a fixed date.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from apps.scheduling.services.metrics_eval import (  # noqa: E402
    DEFAULT_ANCHOR,
    run_cancel_fill,
)

SCENARIOS = ("cancel-fill",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic scheduling evaluation scenarios."
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="cancel-fill")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--anchor", type=str, default=DEFAULT_ANCHOR.isoformat())
    parser.add_argument("--indent", type=int, default=2)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    anchor = date.fromisoformat(args.anchor)
    if args.scenario == "cancel-fill":
        summary = run_cancel_fill(anchor_date=anchor, days=args.days, seed=args.seed)
    else:  # pragma: no cover - argparse restricts the choices
        raise SystemExit(f"unknown scenario: {args.scenario}")
    print(json.dumps(summary, indent=args.indent, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
