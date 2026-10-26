#!/usr/bin/env python
"""Repeated-split evaluation for the no-show model (uncertainty, not a new model).

Runs the existing deterministic pipeline over several split seeds and reports
mean / standard deviation / range for the candidate and the logistic baseline.
The synthetic dataset is built once (seed 42) and is identical across runs; only
the patient-disjoint split and the estimator seed change. No artifact is
written and no registry row is created.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 4),
        "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "n": len(values),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repeated-split no-show evaluation.")
    parser.add_argument("--seeds", default="13,29,42,71,101")
    parser.add_argument("--size", type=int, default=1200)
    parser.add_argument("--dataset-seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/ml")
    args = parser.parse_args(argv)

    from ml.no_show.dataset import build_synthetic_dataset
    from ml.no_show.train import train

    seeds = [int(part) for part in args.seeds.split(",") if part.strip()]
    frame = build_synthetic_dataset(args.dataset_seed, args.size)
    rows = []
    for seed in seeds:
        result = train(seed=seed, dataset=frame, git_sha="repeated-split")
        metrics = result["metrics"]
        rows.append(
            {
                "seed": seed,
                "candidate": {
                    "roc_auc": metrics["candidate"]["roc_auc"],
                    "pr_auc": metrics["candidate"]["pr_auc"],
                    "brier": metrics["candidate"]["brier"],
                    "f1": metrics["candidate"]["f1"],
                },
                "logistic": {
                    "roc_auc": metrics["baseline"]["logistic_regression"]["roc_auc"],
                    "pr_auc": metrics["baseline"]["logistic_regression"]["pr_auc"],
                    "brier": metrics["baseline"]["logistic_regression"]["brier"],
                    "f1": metrics["baseline"]["logistic_regression"]["f1"],
                },
                "gate": metrics["activation_gate"]["decision"],
                "temporal_holdout": metrics.get("temporal_holdout"),
            }
        )
        print(
            f"seed={seed} candidate_auc={rows[-1]['candidate']['roc_auc']:.4f} "
            f"brier={rows[-1]['candidate']['brier']:.4f} gate={rows[-1]['gate']}"
        )

    def collect(model: str, key: str) -> dict[str, float]:
        return _summary([row[model][key] for row in rows])

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "name": "synthetic",
            "size": args.size,
            "dataset_seed": args.dataset_seed,
            "disclosure": (
                "Synthetic records only; this is a methodology demonstration, not a "
                "clinical validation."
            ),
        },
        "seeds": seeds,
        "candidate": {
            key: collect("candidate", key)
            for key in ("roc_auc", "pr_auc", "brier", "f1")
        },
        "logistic_baseline": {
            key: collect("logistic", key)
            for key in ("roc_auc", "pr_auc", "brier", "f1")
        },
        "activation_gate": {
            "activated": sum(1 for row in rows if row["gate"] == "activated"),
            "total": len(rows),
        },
        "runs": rows,
    }

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = BACKEND_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "noshow_repeated_splits.json"
    md_path = out_dir / "noshow_repeated_splits.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# No-show: repeated-split evaluation",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- dataset: synthetic n={args.size}, dataset seed {args.dataset_seed}",
        f"- split/estimator seeds: {seeds}",
        "",
        "| metric | candidate mean ± sd | candidate range | logistic mean ± sd |",
        "|---|---|---|---|",
    ]
    for key in ("roc_auc", "pr_auc", "brier", "f1"):
        cand = report["candidate"][key]
        base = report["logistic_baseline"][key]
        lines.append(
            f"| {key} | {cand['mean']:.4f} ± {cand['std']:.4f} | "
            f"{cand['min']:.4f}-{cand['max']:.4f} | {base['mean']:.4f} ± {base['std']:.4f} |"
        )
    lines += [
        "",
        f"Activation gate passed in {report['activation_gate']['activated']}/"
        f"{report['activation_gate']['total']} splits.",
        "",
        "Synthetic data only; not a clinical claim.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
