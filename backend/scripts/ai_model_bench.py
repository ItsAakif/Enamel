#!/usr/bin/env python
"""Free-model discovery and benchmark CLI.

Usage::

    python scripts/ai_model_bench.py catalogue --out artifacts/evals
    python scripts/ai_model_bench.py run --models id-a,id-b --cases 2 --out artifacts/evals

``run`` discovers candidate free models from the live ``/models`` catalogue
(free pricing + tools + structured output advertised, dynamic router excluded)
or takes an explicit ``--models`` list, then runs the deterministic battery
sequentially with 429/5xx backoff and a hard request cap.

The live benchmark needs ``OPENROUTER_API_KEY`` in the environment. It is a
CLI run, never a test: pytest blocks real sockets, and the benchmark measures
the live catalogue and live model behaviour on purpose.

Exit codes: 0 ok; 1 request budget exhausted or no eligible default; 2 invalid
configuration (missing key, unknown provider, no candidates).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from apps.ai.evals import bench  # noqa: E402

EXIT_OK = 0
EXIT_BUDGET = 1
EXIT_CONFIG = 2

DEFAULT_OUT = "artifacts/evals"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_model_bench.py",
        description="Discover free OpenRouter models and benchmark a shortlist.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    catalogue = sub.add_parser(
        "catalogue", help="Fetch /models and write model-catalogue.json."
    )
    _common_options(catalogue)
    catalogue.add_argument(
        "--pretty", action="store_true", help="Also print a summary of free candidates."
    )

    run = sub.add_parser("run", help="Benchmark a shortlist (or explicit models).")
    _common_options(run)
    run.add_argument(
        "--models",
        default=None,
        help="Comma-separated candidate model ids (skips auto-discovery).",
    )
    run.add_argument(
        "--limit", type=int, default=bench.DEFAULT_SHORTLIST_SIZE, help="Auto-discovery size."
    )
    run.add_argument(
        "--cases", type=int, default=2, help="Cases per category (injection stays >= 6)."
    )
    run.add_argument(
        "--case-limit",
        type=int,
        default=None,
        help="Hard cap on cases per model (battery order is fixed).",
    )
    run.add_argument(
        "--pace",
        type=float,
        default=bench.DEFAULT_PACE_SECONDS,
        help=f"Seconds between logical cases (default: {bench.DEFAULT_PACE_SECONDS:.1f}).",
    )
    run.add_argument(
        "--max-consecutive-429",
        type=int,
        default=bench.MAX_CONSECUTIVE_429,
        help=(
            f"After this many consecutive 429-only cases a model is marked "
            f"unavailable at run time and the run moves on "
            f"(default: {bench.MAX_CONSECUTIVE_429})."
        ),
    )
    run.add_argument(
        "--attempts",
        type=int,
        default=bench.BENCH_MAX_ATTEMPTS_PER_CALL,
        help="HTTP attempts per call (429/5xx/backoff remain honoured).",
    )
    run.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Total request cap (defaults to AI_BENCH_MAX_REQUESTS).",
    )
    run.add_argument(
        "--config",
        default=None,
        help="free-models.json path (default apps/ai/evals/configs/free-models.json).",
    )
    run.add_argument(
        "--recheck",
        action="store_true",
        help=(
            "Re-run only the models marked unavailable in a previous artifact "
            "(see --from; default: newest model-bench-*.json in --out)."
        ),
    )
    run.add_argument(
        "--from",
        dest="from_artifact",
        default=None,
        help="Bench artifact to merge from in --recheck mode.",
    )
    run.add_argument(
        "--pin",
        default=None,
        help=(
            "Pin 'default,fallback' into free-models.json instead of letting "
            "the run choose; use --pin-status to grade the evidence."
        ),
    )
    run.add_argument(
        "--pin-status",
        default="measured",
        help="Evidence status recorded next to --pin (e.g. unverified-under-load).",
    )
    return parser


def _common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Artifacts directory for JSON/MD outputs."
    )


def _headers() -> dict[str, str]:
    api_key = (
        getattr(settings, "OPENROUTER_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
    )
    if not api_key:
        print(
            "missing OPENROUTER_API_KEY: load it into the process environment "
            "(never paste a key into a file, log or artifact)",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CONFIG)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Enamel AI benchmark",
    }


def _settings() -> dict[str, object]:
    return {
        "base_url": getattr(settings, "OPENROUTER_BASE_URL", bench.DEFAULT_BASE_URL),
        "timeout": float(
            getattr(settings, "AI_TIMEOUT_SECONDS", bench.BENCH_TIMEOUT_SECONDS)
        ),
    }


def run_catalogue(args: argparse.Namespace) -> int:
    cfg = _settings()
    headers = _headers()
    with httpx.Client(base_url=str(cfg["base_url"])) as client:
        entries = bench.fetch_catalogue(client, headers=headers, timeout=float(cfg["timeout"]))
    artifact = bench.catalogue_artifact(entries)
    path = bench.write_artifact_json(artifact, Path(args.out) / "model-catalogue.json")
    print(f"wrote {path}")
    print(
        f"total={len(entries)} free_bench_eligible={artifact['free_bench_eligible_count']} "
        f"dynamic_router={len(artifact['dynamic_router'])}"
    )
    if getattr(args, "pretty", False):
        for entry in artifact["free_bench_eligible"]:
            print(f"  {entry['id']}")
    return EXIT_OK


def _load_previous(out_dir: str, explicit: str | None) -> dict[str, object] | None:
    """Load the artifact to merge from (explicit path or newest bench JSON)."""

    if explicit:
        path = Path(explicit)
    else:
        root = Path(out_dir) if os.path.isabs(out_dir) or len(Path(out_dir).parts) > 1 else BACKEND_DIR / out_dir
        candidates = sorted(root.glob("model-bench-*.json"))
        if not candidates:
            return None
        path = candidates[-1]
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    is_bench = data.get("kind") == bench.BENCH_KIND if isinstance(data, dict) else False
    return data if is_bench else None


def run_bench(args: argparse.Namespace) -> int:
    cfg = _settings()
    headers = _headers()
    config_path = (
        Path(args.config)
        if getattr(args, "config", None)
        else BACKEND_DIR / "apps" / "ai" / "evals" / "configs" / "free-models.json"
    )
    max_requests = int(
        args.max_requests
        if args.max_requests is not None
        else getattr(settings, "AI_BENCH_MAX_REQUESTS", 150)
    )

    merge_from: dict[str, object] | None = None
    model_ids: list[str] = []
    if getattr(args, "recheck", False):
        merge_from = _load_previous(args.out, getattr(args, "from_artifact", None))
        if not merge_from:
            print(
                "--recheck needs a previous model-bench-*.json artifact (--from), none found",
                file=sys.stderr,
            )
            return EXIT_CONFIG
        previous = [item for item in merge_from.get("models", []) if isinstance(item, dict)]
        stale = [
            str(item["model_id"])
            for item in previous
            if item.get("score", {}).get("runtime_unavailable")
        ]
        if not stale:
            print(
                "nothing to recheck: no model in the previous artifact was "
                "marked unavailable at run time",
                file=sys.stderr,
            )
            return EXIT_CONFIG
        model_ids = stale
        print(f"recheck: re-running previously unavailable model(s): {', '.join(model_ids)}")

    with httpx.Client(base_url=str(cfg["base_url"])) as client:
        entries = bench.fetch_catalogue(client, headers=headers, timeout=float(cfg["timeout"]))
        if not model_ids:
            if getattr(args, "models", None):
                model_ids = [item.strip() for item in args.models.split(",") if item.strip()]
            else:
                candidates = bench.free_bench_models(entries)
                model_ids = bench.default_shortlist(candidates, limit=int(args.limit))
    if not model_ids:
        print("no candidate models selected; pass --models explicitly", file=sys.stderr)
        return EXIT_CONFIG

    pinned_pair = None
    if getattr(args, "pin", None):
        pinned_pair = [item.strip() for item in str(args.pin).split(",") if item.strip()]

    print(f"benchmarking {len(model_ids)} model(s): {', '.join(model_ids)}")
    report = bench.run_benchmark(
        model_ids,
        client=httpx.Client(base_url=str(cfg["base_url"])),
        headers=headers,
        base_url=str(cfg["base_url"]),
        catalogue_entries=entries,
        cases_per_category=int(args.cases),
        case_limit=args.case_limit,
        pace_seconds=float(args.pace),
        max_consecutive_429=args.max_consecutive_429,
        max_attempts=int(args.attempts),
        max_requests=max_requests,
        seed=1234,
        sleep=time.sleep,
        rng=random.Random(1234),
        artifact_dir=Path(args.out),
        config_path=config_path,
        merge_from=merge_from,
        pinned_pair=pinned_pair,
        pin_status=str(getattr(args, "pin_status", "measured")),
    )
    print(f"wrote {report.get('artifact_path')}")
    print(f"wrote {report.get('markdown_path')}")
    print(f"wrote {config_path}")
    selection = report["selection"]
    pinned = report.get("pinned")
    if pinned:
        print(
            f"\nPinned pair (status: {pinned.get('status')}):\n"
            f"  default : {pinned.get('default')}\n"
            f"  fallback: {pinned.get('fallback')}"
        )
    print(
        "\nSelection:\n"
        f"  default : {selection.get('default')}\n"
        f"  fallback: {selection.get('fallback')}"
    )
    print("\nScores (deterministic order):")
    for item in report["scores"]:
        usage = item["usage"]
        print(
            f"  {item['model_id']}: overall={item['overall_rate']:.2f} "
            f"p50={item['latency_ms']['p50']:.0f}ms p95={item['latency_ms']['p95']:.0f}ms "
            f"errors={item['error_count']} 429s={item['rate_limited_count']} "
            f"unavail={item.get('unavailable_count', 0)} "
            f"empty={item['empty_content_count']} "
            f"tokens={usage['prompt_tokens'] + usage['completion_tokens']}"
            + (
                " RUNTIME-UNAVAILABLE"
                if item.get("runtime_unavailable")
                else ""
            )
        )
    totals = report["totals"]
    print(
        f"\nrequests={totals['requests']}/{totals['max_requests']}"
        + (" (budget exhausted)" if totals["budget_exhausted"] else "")
    )
    if totals["budget_exhausted"]:
        return EXIT_BUDGET
    if not selection.get("default"):
        print("no eligible default model; inspect the bench artifact", file=sys.stderr)
        return EXIT_BUDGET
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "catalogue":
        return run_catalogue(args)
    return run_bench(args)


if __name__ == "__main__":
    raise SystemExit(main())
