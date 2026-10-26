#!/usr/bin/env python
"""AI evaluation CLI (T126).

Usage::

    python scripts/ai_evals.py run --suite all --out artifacts/evals/latest.json
    python scripts/ai_evals.py run --suite all --compare artifacts/evals/latest.json

Exit codes: 0 pass, 1 regression/failure, 2 invalid configuration.

The default provider is the offline deterministic mock; ``--provider openrouter``
requires credentials and is never used in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from apps.ai.evals import regression, report, runner  # noqa: E402

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_CONFIG = 2

PROVIDERS = ("mock", "openrouter")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_evals.py", description="Run the Enamel AI evaluation suites."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run one or all evaluation suites.")
    run_parser.add_argument(
        "--suite", default="all", choices=["all", *runner.SUITE_ORDER]
    )
    run_parser.add_argument(
        "--out", default="artifacts/evals/latest.json", help="JSON output path."
    )
    run_parser.add_argument(
        "--compare", default=None, help="Baseline JSON to compare against."
    )
    run_parser.add_argument("--provider", default="mock", choices=PROVIDERS)
    run_parser.add_argument("--seed", type=int, default=1234)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        current = runner.run(
            suite=args.suite, provider_name=args.provider, seed=args.seed
        )
    except runner.EvalConfigError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        report.validate(current)
    except Exception as exc:  # noqa: BLE001 - schema failure is a config error
        print(f"report failed schema validation: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    json_path, md_path = report.write(current, Path(args.out))
    totals = current["totals"]
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    print(
        f"suites={len(current['suites'])} total={totals['total']} "
        f"passed={totals['passed']} failed={totals['failed']} "
        f"duration_ms={totals['duration_ms']}"
    )

    code = EXIT_PASS

    if args.compare:
        baseline_path = Path(args.compare)
        if not baseline_path.exists():
            print(f"baseline not found: {baseline_path}", file=sys.stderr)
            return EXIT_CONFIG
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        regressions = regression.compare(current, baseline)
        if regressions:
            for item in regressions:
                print(f"REGRESSION: {item}", file=sys.stderr)
            code = EXIT_REGRESSION

    if totals["failed"] > 0:
        code = EXIT_REGRESSION

    return code


if __name__ == "__main__":
    raise SystemExit(main())
