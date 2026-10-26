"""Evaluate the CV fixture pipeline and write its metrics + report (T129).

Usage::

    python scripts/cv_evaluate.py --config ml/cv/configs/fixture.yaml --out artifacts/cv

Writes ``metrics.json`` and ``report.md`` under the output directory. The report
contains an explicit limitations section: the evaluation is fixture-only and
makes no clinical claim. The fixture dataset is generated deterministically if it
is not already present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _resolve(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def document_hash(document: dict) -> str:
    """SHA-256 over the canonical metrics document (deterministic across runs)."""

    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the CV fixture pipeline.")
    parser.add_argument("--config", default="ml/cv/configs/fixture.yaml")
    parser.add_argument("--out", default="artifacts/cv")
    parser.add_argument("--fixtures", default="fixtures/cv")
    options = parser.parse_args(argv)

    import numpy as np
    from PIL import Image

    from ml.cv.evaluate import LIMITATIONS, evaluate, render_report
    from ml.cv.fixtures import build_dataset, load_dataset
    from ml.cv.infer import predict
    from ml.cv.preprocess import load_config

    config = load_config(_resolve(options.config))
    evaluation_config = config.get("evaluation", {})
    target_label = str(evaluation_config.get("target_label", "radiopacity"))
    bins = int(evaluation_config.get("calibration_bins", 5))
    threshold = float(evaluation_config.get("decision_threshold", 0.5))

    fixtures_dir = _resolve(options.fixtures)
    if not (fixtures_dir / "labels.json").exists():
        build_dataset(fixtures_dir)
    dataset = load_dataset(fixtures_dir)

    y_true: list[int] = []
    y_pred: list[int] = []
    confidences: list[float] = []
    for item in dataset["items"]:
        image = np.asarray(Image.open(fixtures_dir / item["image"]))
        findings = predict(image, {"image": item["image"]}, config)
        confidence = max((finding.confidence for finding in findings), default=0.0)
        y_true.append(int(item["label"]))
        y_pred.append(1 if findings else 0)
        confidences.append(confidence)

    metrics = evaluate(y_true, y_pred, confidences, threshold=threshold, bins=bins)

    model = config.get("model", {})
    provenance = {
        "dataset_name": dataset["name"],
        "dataset_version": dataset["version"],
        "licence": dataset["licence"],
        "dataset_hash": dataset["hash"],
        "generator": dataset["generator"],
        "target_label": target_label,
        "model_name": str(model.get("name", "baseline")),
        "model_version": str(model.get("version", "0")),
    }

    out_dir = _resolve(options.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "metrics": metrics,
        "provenance": provenance,
        "config": config,
        "limitations": LIMITATIONS,
    }
    document["metrics_hash"] = document_hash(document)
    (out_dir / "metrics.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        render_report(metrics, provenance=provenance, config=config), encoding="utf-8"
    )

    print(f"dataset={dataset['name']} n={metrics['n']} positives={metrics['positives']}")
    print(
        "sensitivity={sensitivity} specificity={specificity} precision={precision} "
        "recall={recall}".format(**metrics)
    )
    print(f"metrics_hash={document['metrics_hash']}")
    print(f"metrics={out_dir / 'metrics.json'}")
    print(f"report={out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
