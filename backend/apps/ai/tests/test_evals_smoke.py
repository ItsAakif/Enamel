"""Evaluation-suite smoke test (T126).

Runs the full suite offline with the mock provider and asserts: everything
passes, the JSON report validates against the committed schema, a rerun is
deterministic, and the CLI honours its exit codes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from django.conf import settings

from apps.ai.evals import report, runner


def _load_cli():
    """Load ``scripts/ai_evals.py`` by path (scripts is not a package)."""

    path = Path(settings.BASE_DIR) / "scripts" / "ai_evals.py"
    spec = importlib.util.spec_from_file_location("enamel_ai_evals_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_suites_pass_and_report_matches_schema(db):
    report_dict = runner.run(suite="all", provider_name="mock", cleanup=False)

    assert report_dict["totals"]["total"] > 0
    assert report_dict["totals"]["failed"] == 0
    assert {suite["suite"] for suite in report_dict["suites"]} == set(runner.SUITE_ORDER)

    # Validates against the committed JSON schema.
    report.validate(report_dict)
    assert report.to_markdown(report_dict).startswith("# AI evaluation report")


def test_eval_rerun_is_deterministic(db):
    first = runner.run(suite="all", provider_name="mock", cleanup=False)
    second = runner.run(suite="all", provider_name="mock", cleanup=False)

    assert runner.fingerprint(first) == runner.fingerprint(second)


def test_cli_exit_codes(db, tmp_path):
    cli = _load_cli()
    out = tmp_path / "latest.json"

    # A clean run exits 0 and writes a schema-valid report.
    assert cli.main(["run", "--suite", "all", "--out", str(out)]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    report.validate(written)
    assert out.with_suffix(".md").exists()

    # Comparing against an identical baseline exits 0.
    assert cli.main(["run", "--suite", "all", "--out", str(out), "--compare", str(out)]) == 0

    # A baseline case that is missing from the current run is a regression.
    tampered = tmp_path / "baseline.json"
    baseline = json.loads(out.read_text(encoding="utf-8"))
    baseline["suites"][0]["cases"].append(
        {
            "suite": "tools",
            "case": "tools.removed.case",
            "status": "pass",
            "reason": "",
            "latency_ms": 0,
            "tokens": 0,
            "cost_minor": 0,
        }
    )
    tampered.write_text(json.dumps(baseline), encoding="utf-8")
    assert cli.main(["run", "--suite", "all", "--out", str(out), "--compare", str(tampered)]) == 1

    # openrouter without credentials is an invalid configuration (exit 2).
    assert cli.main(["run", "--suite", "all", "--provider", "openrouter", "--out", str(out)]) == 2
