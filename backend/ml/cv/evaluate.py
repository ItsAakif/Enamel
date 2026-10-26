"""Fixture evaluation metrics for the CV pipeline (T129).

Every number is computed on the synthetic fixture set only. The metrics document
and the Markdown report both state that this is fixture-only and not clinically
validated; no result here is a claim about real patients.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

METRIC_PRECISION = 6
DEFAULT_BINS = 5


def _round(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if np.isnan(number) or np.isinf(number):
        return None
    return round(number, METRIC_PRECISION)


def confusion_counts(y_true: Sequence[int], y_pred: Sequence[int]) -> dict[str, int]:
    values = np.asarray(y_true).astype(int)
    predicted = np.asarray(y_pred).astype(int)
    matrix = confusion_matrix(values, predicted, labels=[0, 1])
    tn, fp, fn, tp = (int(matrix[0][0]), int(matrix[0][1]), int(matrix[1][0]), int(matrix[1][1]))
    return {"true_negative": tn, "false_positive": fp, "false_negative": fn, "true_positive": tp}


def calibration_bins(
    y_true: Sequence[int],
    confidences: Sequence[float],
    *,
    bins: int = DEFAULT_BINS,
) -> list[dict[str, Any]]:
    """Equal-width calibration curve data (predicted vs observed)."""

    values = np.asarray(y_true).astype(int)
    scores = np.asarray(confidences, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    curve: list[dict[str, Any]] = []
    for index in range(bins):
        low, high = float(edges[index]), float(edges[index + 1])
        if index == bins - 1:
            mask = (scores >= low) & (scores <= high)
        else:
            mask = (scores >= low) & (scores < high)
        count = int(mask.sum())
        if count == 0:
            curve.append(
                {
                    "lower": round(low, 4),
                    "upper": round(high, 4),
                    "count": 0,
                    "mean_confidence": None,
                    "observed_rate": None,
                }
            )
            continue
        curve.append(
            {
                "lower": round(low, 4),
                "upper": round(high, 4),
                "count": count,
                "mean_confidence": _round(float(scores[mask].mean())),
                "observed_rate": _round(float(values[mask].mean())),
            }
        )
    return curve


def evaluate(
    y_true: Sequence[int],
    y_pred: Sequence[int],
    confidences: Sequence[float],
    *,
    threshold: float = 0.5,
    bins: int = DEFAULT_BINS,
) -> dict[str, Any]:
    """Return the full fixture metric block."""

    values = np.asarray(y_true).astype(int)
    predicted = np.asarray(y_pred).astype(int)
    counts = confusion_counts(values, predicted)
    tn = counts["true_negative"]
    fp = counts["false_positive"]

    specificity = tn / (tn + fp) if (tn + fp) else None

    return {
        "n": int(values.size),
        "positives": int(values.sum()),
        "threshold": round(float(threshold), 4),
        "sensitivity": _round(recall_score(values, predicted, zero_division=0)),
        "specificity": _round(specificity) if specificity is not None else None,
        "precision": _round(precision_score(values, predicted, zero_division=0)),
        "recall": _round(recall_score(values, predicted, zero_division=0)),
        "f1": _round(f1_score(values, predicted, zero_division=0)),
        "confusion_matrix": counts,
        "calibration_bins": calibration_bins(values, confidences, bins=bins),
    }


LIMITATIONS = (
    "Fixture-only evaluation: the images and labels are synthetic and generated "
    "locally, so these metrics do NOT estimate performance on real patients. "
    "The model is a transparent intensity/threshold baseline, not a trained "
    "network. No clinical sensitivity/specificity is claimed and this pipeline "
    "is not validated for diagnostic use. Any deployment on real data requires a "
    "licensed dataset, external validation and regulatory review."
)


def render_report(metrics: dict[str, Any], *, provenance: dict[str, Any], config: dict[str, Any]) -> str:
    """Render the Markdown evaluation report (with an explicit limitations section)."""

    cm = metrics["confusion_matrix"]
    lines = [
        "# CV pipeline evaluation (fixture set)",
        "",
        "**This is a fixture-only evaluation. It is not clinically validated.**",
        "",
        "## Configuration",
        "",
        f"- Model: `{provenance.get('model_name')}` version `{provenance.get('model_version')}`",
        f"- Preprocessing resize: `{config.get('preprocess', {}).get('resize')}`",
        f"- Normalisation: `{config.get('preprocess', {}).get('normalization')}`",
        f"- Decision threshold: `{metrics['threshold']}`",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "|---|---|",
        f"| n | {metrics['n']} |",
        f"| positives | {metrics['positives']} |",
        f"| sensitivity (recall) | {metrics['sensitivity']} |",
        f"| specificity | {metrics['specificity']} |",
        f"| precision | {metrics['precision']} |",
        f"| f1 | {metrics['f1']} |",
        "",
        "## Confusion matrix",
        "",
        "| | predicted negative | predicted positive |",
        "|---|---|---|",
        f"| actual negative | {cm['true_negative']} | {cm['false_positive']} |",
        f"| actual positive | {cm['false_negative']} | {cm['true_positive']} |",
        "",
        "## Calibration",
        "",
        "| bin | count | mean confidence | observed rate |",
        "|---|---|---|---|",
    ]
    for bucket in metrics["calibration_bins"]:
        lines.append(
            f"| {bucket['lower']}-{bucket['upper']} | {bucket['count']} | "
            f"{bucket['mean_confidence']} | {bucket['observed_rate']} |"
        )
    lines += [
        "",
        "## Provenance",
        "",
        f"- Dataset: `{provenance.get('dataset_name')}` version `{provenance.get('dataset_version')}`",
        f"- Licence: {provenance.get('licence')}",
        f"- Dataset hash: `{provenance.get('dataset_hash')}`",
        f"- Generator: `{provenance.get('generator')}`",
        "",
        "## Limitations",
        "",
        LIMITATIONS,
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "DEFAULT_BINS",
    "LIMITATIONS",
    "calibration_bins",
    "confusion_counts",
    "evaluate",
    "render_report",
]
