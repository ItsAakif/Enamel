"""Evaluation report rendering and schema validation (T126)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(report: dict[str, Any]) -> None:
    """Raise ``jsonschema.ValidationError`` if ``report`` violates the schema."""

    jsonschema.validate(instance=report, schema=load_schema())


def to_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# AI evaluation report",
        "",
        f"- provider: `{report['provider']}`",
        f"- seed: `{report['seed']}`",
        f"- generated_at: `{report['generated_at']}`",
        f"- totals: **{totals['passed']}/{totals['total']} passed** "
        f"({totals['failed']} failed) in {totals['duration_ms']} ms",
        "",
        "| suite | passed | failed | total | duration_ms |",
        "|---|---|---|---|---|",
    ]
    for suite in report["suites"]:
        lines.append(
            f"| {suite['suite']} | {suite['passed']} | {suite['failed']} | "
            f"{suite['total']} | {suite['duration_ms']} |"
        )
    lines.append("")
    for suite in report["suites"]:
        lines.append(f"## {suite['suite']}")
        lines.append("")
        for case in suite["cases"]:
            mark = "PASS" if case["status"] == "pass" else "FAIL"
            detail = f" -- {case['reason']}" if case["reason"] else ""
            lines.append(f"- **{mark}** `{case['case']}`{detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write(
    report: dict[str, Any], json_path: Path, md_path: Path | None = None
) -> tuple[Path, Path]:
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if md_path is None:
        md_path = json_path.with_suffix(".md")
    md_path = Path(md_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path


__all__ = ["SCHEMA_PATH", "load_schema", "to_markdown", "validate", "write"]
