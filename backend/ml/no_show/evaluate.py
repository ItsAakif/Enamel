"""Metric, calibration and error-analysis computation (T131).

Every number in the metrics document comes from here. Two rules:

* **No NaN ever reaches the document.** A subgroup with only one observed class
  has no ROC AUC; it is reported as ``None`` with an explicit ``note`` rather
  than as ``NaN`` (which is not even valid JSON). A dataset with a single class
  overall is a hard error raised by :func:`require_both_classes`.
* **Deterministic rounding.** Floats are rounded to
  ``config.METRIC_PRECISION`` so the metrics hash is stable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .config import CALIBRATION_BINS, METRIC_PRECISION


def _round(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if np.isnan(number) or np.isinf(number):
        return None
    return round(number, METRIC_PRECISION)


class DegenerateDatasetError(ValueError):
    """Raised when a split has only one observed class."""


def require_both_classes(y_true: Sequence[int], *, where: str) -> None:
    """Abort loudly when a split cannot support classification metrics."""

    values = np.asarray(y_true)
    if values.size == 0:
        raise DegenerateDatasetError(f"{where} is empty")
    positives = int(values.sum())
    if positives == 0 or positives == values.size:
        raise DegenerateDatasetError(
            f"{where} contains a single class "
            f"(positives={positives}, n={values.size}); metrics would be undefined"
        )


def choose_threshold(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    grid: Sequence[float],
) -> float:
    """Pick the F1-maximising threshold on the *validation* set only."""

    values = np.asarray(y_true)
    scores = np.asarray(probabilities, dtype=float)
    best_threshold = float(grid[0])
    best_score = -1.0
    for threshold in grid:
        predicted = (scores >= threshold).astype(int)
        score = f1_score(values, predicted, zero_division=0)
        if score > best_score:
            best_score = float(score)
            best_threshold = float(threshold)
    return round(best_threshold, 4)


def binary_metrics(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    *,
    threshold: float,
) -> dict[str, Any]:
    """Return the full metric block for one split at ``threshold``."""

    values = np.asarray(y_true)
    scores = np.asarray(probabilities, dtype=float)
    predicted = (scores >= threshold).astype(int)

    require_both_classes(values, where="metrics")
    matrix = confusion_matrix(values, predicted, labels=[0, 1])
    tn, fp, fn, tp = (int(matrix[0][0]), int(matrix[0][1]), int(matrix[1][0]), int(matrix[1][1]))

    return {
        "n": int(values.size),
        "positives": int(values.sum()),
        "roc_auc": _round(roc_auc_score(values, scores)),
        "pr_auc": _round(average_precision_score(values, scores)),
        "brier": _round(brier_score_loss(values, scores)),
        "threshold": round(float(threshold), 4),
        "precision": _round(precision_score(values, predicted, zero_division=0)),
        "recall": _round(recall_score(values, predicted, zero_division=0)),
        "f1": _round(f1_score(values, predicted, zero_division=0)),
        "confusion": {
            "true_negative": tn,
            "false_positive": fp,
            "false_negative": fn,
            "true_positive": tp,
        },
        "predicted_positive_rate": _round(float(predicted.mean())),
        "mean_predicted": _round(float(scores.mean())),
        "observed_rate": _round(float(values.mean())),
    }


def calibration_bins(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    *,
    bins: int = CALIBRATION_BINS,
) -> list[dict[str, Any]]:
    """Equal-width calibration curve data (predicted vs observed)."""

    values = np.asarray(y_true)
    scores = np.asarray(probabilities, dtype=float)
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
                    "mean_predicted": None,
                    "observed_rate": None,
                }
            )
            continue
        curve.append(
            {
                "lower": round(low, 4),
                "upper": round(high, 4),
                "count": count,
                "mean_predicted": _round(float(scores[mask].mean())),
                "observed_rate": _round(float(values[mask].mean())),
            }
        )
    return curve


def calibration_error(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    *,
    bins: int = CALIBRATION_BINS,
) -> float:
    """Return the count-weighted mean absolute bin calibration error."""

    curve = calibration_bins(y_true, probabilities, bins=bins)
    total = sum(item["count"] for item in curve)
    if total == 0:
        return 0.0
    error = 0.0
    for item in curve:
        if item["count"] == 0:
            continue
        error += item["count"] * abs(
            float(item["mean_predicted"]) - float(item["observed_rate"])
        )
    return round(error / total, METRIC_PRECISION)


def subgroup_error_analysis(
    y_true: Sequence[int],
    probabilities: Sequence[float],
    groups: Mapping[str, Sequence[Any]],
    *,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """Per-subgroup error analysis.

    ``groups`` maps a subgroup name to a per-row label. A subgroup with a
    single observed class reports ``roc_auc=None`` plus a note, never NaN.
    """

    values = np.asarray(y_true)
    scores = np.asarray(probabilities, dtype=float)
    analysis: dict[str, dict[str, Any]] = {}
    for name, labels in groups.items():
        labels_array = np.asarray([str(label) for label in labels])
        buckets: dict[str, Any] = {}
        for label in sorted(set(labels_array.tolist())):
            mask = labels_array == label
            count = int(mask.sum())
            sub_values = values[mask]
            sub_scores = scores[mask]
            positives = int(sub_values.sum())
            predicted = (sub_scores >= threshold).astype(int)
            entry: dict[str, Any] = {
                "n": count,
                "positives": positives,
                "observed_rate": _round(float(sub_values.mean())),
                "mean_predicted": _round(float(sub_scores.mean())),
                "brier": _round(brier_score_loss(sub_values, sub_scores)),
                "precision": _round(
                    precision_score(sub_values, predicted, zero_division=0)
                ),
                "recall": _round(recall_score(sub_values, predicted, zero_division=0)),
            }
            if 0 < positives < count:
                entry["roc_auc"] = _round(roc_auc_score(sub_values, sub_scores))
                entry["note"] = None
            else:
                entry["roc_auc"] = None
                entry["note"] = (
                    "single observed class in this subgroup; ROC AUC undefined"
                )
            buckets[label] = entry
        analysis[name] = buckets
    return analysis


def permutation_importance(
    model: Any,
    features: np.ndarray,
    y_true: Sequence[int],
    *,
    feature_names: Sequence[str],
    seed: int,
    repeats: int = 5,
) -> list[dict[str, Any]]:
    """Global permutation importance, ordered most-important first.

    This is a hand-rolled version of ``sklearn.inspection.permutation_importance``
    that calls ``predict_proba``/``roc_auc_score`` directly. It is identical in
    meaning (drop in ROC AUC when one column is shuffled) but skips the scorer
    validation overhead, which dominates for a calibrated ensemble on a small
    holdout. Determinism comes from the fixed ``RandomState(seed)``.

    These weights are the honest global explanation surfaced by the inference
    API. They describe the model, not an individual appointment; the API labels
    them as such instead of pretending to a per-prediction attribution.
    """

    values = np.asarray(y_true)
    matrix = np.asarray(features, dtype=float)
    rng = np.random.RandomState(seed)
    baseline = roc_auc_score(values, model.predict_proba(matrix)[:, 1])

    pairs: list[dict[str, Any]] = []
    for column, name in enumerate(feature_names):
        drops: list[float] = []
        for _ in range(max(1, repeats)):
            shuffled = matrix.copy()
            rng.shuffle(shuffled[:, column])
            score = roc_auc_score(values, model.predict_proba(shuffled)[:, 1])
            drops.append(float(baseline - score))
        pairs.append({"feature": name, "importance": _round(float(np.mean(drops)))})
    pairs.sort(key=lambda item: (-(item["importance"] or 0.0), item["feature"]))
    return pairs


__all__ = [
    "DegenerateDatasetError",
    "binary_metrics",
    "calibration_bins",
    "calibration_error",
    "choose_threshold",
    "permutation_importance",
    "require_both_classes",
    "subgroup_error_analysis",
]
