"""Leakage-safe training, baseline, candidate and activation gate (T131).

Pipeline (ADR-010, ARCHITECTURE.md section 15)::

    synthetic (or supplied) frame
      -> patient-grouped split (GroupShuffleSplit, seed 42)
         -> fit / validation / holdout  (all patient-disjoint)
      -> baseline: prior-probability + logistic regression
      -> candidate: HistGradientBoostingClassifier + isotonic calibration
      -> threshold chosen on validation (F1)
      -> holdout metrics, calibration bins, subgroup errors
      -> activation gate: candidate ROC AUC and Brier must not be worse
      -> metrics document + hash

Determinism
-----------
The whole function is a pure function of ``(seed, dataset, candidate_factory)``.
Nothing reads the clock except the ``trained_at`` field, which is excluded from
:func:`metrics_hash`, so ``--check`` reproduces an identical hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import evaluate
from .config import (
    BASELINE_LOGISTIC,
    CANDIDATE_ALGORITHM,
    DEFAULT_DATASET_SIZE,
    DEFAULT_SEED,
    FEATURE_NAMES,
    METRIC_PRECISION,
    TARGET,
    TEMPORAL_TEST_SIZE,
    TEST_SIZE,
    THRESHOLD_GRID,
)
from .dataset import build_synthetic_dataset, dataset_summary, patient_overlap
from .features import FEATURE_DEFINITIONS, encode_record

#: A candidate factory takes the seed and returns a *fitted-ready* estimator.
CandidateFactory = Callable[[int], Any]


class SplitLeakageError(AssertionError):
    """Raised when a patient appears on both sides of a split."""


def encode_frame(frame: pd.DataFrame) -> np.ndarray:
    """Encode a frame's feature columns into the canonical float matrix."""

    matrix = np.array(
        [encode_record(record) for record in frame[list(FEATURE_NAMES)].to_dict("records")],
        dtype=float,
    )
    return matrix


def assert_disjoint(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    left_name: str,
    right_name: str,
) -> None:
    """Raise :class:`SplitLeakageError` when a patient spans both sides."""

    overlap = patient_overlap(left, right)
    if overlap:
        raise SplitLeakageError(
            f"patient overlap between {left_name} and {right_name}: "
            f"{sorted(overlap)[:5]}"
        )


def group_split(
    frame: pd.DataFrame,
    *,
    seed: int = DEFAULT_SEED,
    test_size: float = TEST_SIZE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Patient-grouped split; raises :class:`SplitLeakageError` if leaked."""

    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    groups = frame["patient_id"].to_numpy()
    train_idx, test_idx = next(splitter.split(frame, groups=groups))
    train = frame.iloc[train_idx].reset_index(drop=True)
    test = frame.iloc[test_idx].reset_index(drop=True)
    assert_disjoint(train, test, left_name="train", right_name="test")
    return train, test


def three_way_split(
    frame: pd.DataFrame,
    *,
    seed: int = DEFAULT_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split into ``(fit, validation, holdout)``, all patient-disjoint.

    The validation slice exists so the decision threshold is chosen on data the
    candidate never fitted on; the holdout slice is untouched until the final
    numbers are computed.
    """

    train_val, holdout = group_split(frame, seed=seed, test_size=TEST_SIZE)
    fit, validation = group_split(train_val, seed=seed + 1, test_size=0.25)
    assert_disjoint(fit, holdout, left_name="fit", right_name="holdout")
    assert_disjoint(validation, holdout, left_name="validation", right_name="holdout")
    return fit, validation, holdout


def temporal_split(
    frame: pd.DataFrame,
    *,
    test_size: float = TEMPORAL_TEST_SIZE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split: newest ``test_size`` of rows are the test side."""

    ordered = frame.sort_values(by="start_at", kind="mergesort").reset_index(drop=True)
    cut = int(len(ordered) * (1.0 - test_size))
    return ordered.iloc[:cut].reset_index(drop=True), ordered.iloc[cut:].reset_index(drop=True)


def _fit_baselines(
    fit: pd.DataFrame,
    *,
    seed: int,
) -> dict[str, Any]:
    """Fit the prior and logistic baselines and return their holdout metrics."""

    y_fit = fit[TARGET].to_numpy()
    x_fit = encode_frame(fit)

    prior = DummyClassifier(strategy="prior", random_state=seed)
    prior.fit(x_fit, y_fit)

    logistic = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(max_iter=2000, random_state=seed),
            ),
        ]
    )
    logistic.fit(x_fit, y_fit)

    return {
        "prior_estimator": prior,
        "logistic_estimator": logistic,
        "fit_target_rate": round(float(y_fit.mean()), METRIC_PRECISION),
    }


def _metrics_for(
    estimator: Any,
    frame: pd.DataFrame,
    *,
    threshold: float,
) -> dict[str, Any]:
    x = encode_frame(frame)
    probabilities = estimator.predict_proba(x)[:, 1]
    return evaluate.binary_metrics(
        frame[TARGET].to_numpy(), probabilities, threshold=threshold
    )


def _probabilities(estimator: Any, frame: pd.DataFrame) -> np.ndarray:
    return estimator.predict_proba(encode_frame(frame))[:, 1]


def metrics_hash(metrics: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the reproducible part of a metrics document.

    ``trained_at``, ``git_sha`` and ``artifact`` are excluded: they are either
    volatile or environment metadata, and including them would make ``--check``
    fail for reasons that have nothing to do with the model.
    """

    payload = {
        "activation_gate": metrics.get("activation_gate"),
        "baseline": metrics.get("baseline"),
        "calibration_bins": metrics.get("calibration_bins"),
        "candidate": metrics.get("candidate"),
        "dataset": metrics.get("dataset"),
        "feature_importance": metrics.get("feature_importance"),
        "features": metrics.get("features"),
        "seed": metrics.get("seed"),
        "sklearn_version": metrics.get("sklearn_version"),
        "split": metrics.get("split"),
        "subgroup_analysis": metrics.get("subgroup_analysis"),
        "temporal_holdout": metrics.get("temporal_holdout"),
        "threshold": metrics.get("threshold"),
        "version": metrics.get("version"),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _subgroup_groups(frame: pd.DataFrame) -> dict[str, list[str]]:
    groups = {
        "appointment_type": frame["appointment_type"].tolist(),
        "source": frame["source"].tolist(),
        "lead_time_band": frame["lead_time_band"].tolist(),
    }
    return groups


def train(
    *,
    seed: int = DEFAULT_SEED,
    dataset: pd.DataFrame | None = None,
    dataset_size: int = DEFAULT_DATASET_SIZE,
    candidate_factory: CandidateFactory | None = None,
    calibrate: bool = True,
    include_permutation_importance: bool = True,
    permutation_repeats: int = 5,
    include_temporal: bool = True,
    git_sha: str = "unknown",
    trained_at: str | None = None,
) -> dict[str, Any]:
    """Run the whole pipeline and return the metrics, artifact and decision."""

    frame = dataset if dataset is not None else build_synthetic_dataset(seed, dataset_size)
    if len(frame) == 0:
        raise evaluate.DegenerateDatasetError("the dataset is empty")
    evaluate.require_both_classes(frame[TARGET].to_numpy(), where="dataset")

    summary = dataset_summary(frame)
    version = f"noshow-{seed}-{summary['hash'][:12]}"

    fit, validation, holdout = three_way_split(frame, seed=seed)
    for name, part in (("fit", fit), ("validation", validation), ("holdout", holdout)):
        evaluate.require_both_classes(part[TARGET].to_numpy(), where=name)

    # --- baselines ---------------------------------------------------------
    baselines = _fit_baselines(fit, seed=seed)
    prior_holdout = _probabilities(baselines["prior_estimator"], holdout)
    logistic_holdout = _probabilities(baselines["logistic_estimator"], holdout)

    # --- candidate ---------------------------------------------------------
    if candidate_factory is not None:
        estimator = candidate_factory(seed)
    else:
        estimator = HistGradientBoostingClassifier(
            random_state=seed,
            learning_rate=0.07,
            max_iter=100,
            min_samples_leaf=20,
            l2_regularization=0.5,
        )
    if calibrate:
        estimator = CalibratedClassifierCV(estimator, method="isotonic", cv=3)
    estimator.fit(encode_frame(fit), fit[TARGET].to_numpy())

    validation_probabilities = _probabilities(estimator, validation)
    threshold = evaluate.choose_threshold(
        validation[TARGET].to_numpy(), validation_probabilities, THRESHOLD_GRID
    )

    candidate_holdout = _probabilities(estimator, holdout)
    candidate_metrics = evaluate.binary_metrics(
        holdout[TARGET].to_numpy(), candidate_holdout, threshold=threshold
    )
    prior_metrics = evaluate.binary_metrics(
        holdout[TARGET].to_numpy(), prior_holdout, threshold=threshold
    )
    logistic_metrics = evaluate.binary_metrics(
        holdout[TARGET].to_numpy(), logistic_holdout, threshold=threshold
    )

    # --- activation gate ---------------------------------------------------
    epsilon = 10 ** (-(METRIC_PRECISION + 2))
    roc_not_worse = candidate_metrics["roc_auc"] >= logistic_metrics["roc_auc"] - epsilon
    brier_not_worse = candidate_metrics["brier"] <= logistic_metrics["brier"] + epsilon
    gate_passed = bool(roc_not_worse and brier_not_worse)
    decision = "activated" if gate_passed else "rejected"

    # --- secondary temporal check -----------------------------------------
    if include_temporal:
        temporal_fit, temporal_test = temporal_split(frame)
        evaluate.require_both_classes(
            temporal_test[TARGET].to_numpy(), where="temporal holdout"
        )
        temporal_estimator = CalibratedClassifierCV(
            HistGradientBoostingClassifier(
                random_state=seed,
                learning_rate=0.07,
                max_iter=100,
                min_samples_leaf=20,
                l2_regularization=0.5,
            ),
            method="isotonic",
            cv=2,
        )
        temporal_estimator.fit(
            encode_frame(temporal_fit), temporal_fit[TARGET].to_numpy()
        )
        temporal_probabilities = _probabilities(temporal_estimator, temporal_test)
        temporal_metrics = evaluate.binary_metrics(
            temporal_test[TARGET].to_numpy(),
            temporal_probabilities,
            threshold=evaluate.choose_threshold(
                temporal_test[TARGET].to_numpy(), temporal_probabilities, THRESHOLD_GRID
            ),
        )
        temporal_metrics["patient_overlap"] = len(
            patient_overlap(temporal_fit, temporal_test)
        )
        temporal_metrics["note"] = (
            "Secondary chronological check. Patients may legitimately appear in "
            "both periods; only the grouped split asserts zero overlap."
        )
    else:
        temporal_metrics = {"skipped": True}

    # --- importance + calibration -----------------------------------------
    if include_permutation_importance:
        importance = evaluate.permutation_importance(
            estimator,
            encode_frame(holdout),
            holdout[TARGET].to_numpy(),
            feature_names=list(FEATURE_NAMES),
            seed=seed,
            repeats=permutation_repeats,
        )
    else:
        importance = []

    bins = evaluate.calibration_bins(
        holdout[TARGET].to_numpy(), candidate_holdout
    )

    metrics: dict[str, Any] = {
        "version": version,
        "seed": seed,
        "algorithm": CANDIDATE_ALGORITHM,
        "sklearn_version": sklearn.__version__,
        "git_sha": git_sha or "unknown",
        "trained_at": trained_at or datetime.now(UTC).isoformat(),
        "dataset": summary,
        "features": [dict(item) for item in FEATURE_DEFINITIONS],
        "feature_names": list(FEATURE_NAMES),
        "split": {
            "method": "GroupShuffleSplit",
            "seed": seed,
            "test_size": TEST_SIZE,
            "n_fit": int(len(fit)),
            "n_validation": int(len(validation)),
            "n_holdout": int(len(holdout)),
            "fit_patients": int(fit["patient_id"].nunique()),
            "validation_patients": int(validation["patient_id"].nunique()),
            "holdout_patients": int(holdout["patient_id"].nunique()),
            "patient_overlap": 0,
        },
        "threshold": threshold,
        "baseline": {
            "primary": BASELINE_LOGISTIC,
            "prior_probability": prior_metrics,
            "logistic_regression": logistic_metrics,
            "fit_target_rate": baselines["fit_target_rate"],
        },
        "candidate": candidate_metrics,
        "temporal_holdout": temporal_metrics,
        "activation_gate": {
            "passed": gate_passed,
            "decision": decision,
            "roc_auc_not_worse": bool(roc_not_worse),
            "brier_not_worse": bool(brier_not_worse),
            "rule": (
                "candidate ROC AUC >= logistic ROC AUC and candidate Brier <= "
                "logistic Brier on the patient-disjoint holdout"
            ),
            "compared_to": BASELINE_LOGISTIC,
        },
        "calibration_bins": bins,
        "calibration_error": evaluate.calibration_error(
            holdout[TARGET].to_numpy(), candidate_holdout
        ),
        "subgroup_analysis": evaluate.subgroup_error_analysis(
            holdout[TARGET].to_numpy(),
            candidate_holdout,
            _subgroup_groups(holdout),
            threshold=threshold,
        ),
        "feature_importance": importance,
        "disclosure": (
            "Synthetic-data, experimental, assistive signal only. Not a "
            "clinical judgement and not clinically validated."
        ),
    }
    metrics["metrics_hash"] = metrics_hash(metrics)

    artifact = {
        "model": estimator,
        "feature_names": list(FEATURE_NAMES),
        "threshold": threshold,
        "version": version,
        "algorithm": CANDIDATE_ALGORITHM,
        "dataset_hash": summary["hash"],
        "sklearn_version": sklearn.__version__,
        "is_synthetic": True,
        "disclosure": metrics["disclosure"],
    }

    return {
        "metrics": metrics,
        "artifact": artifact,
        "metrics_hash": metrics["metrics_hash"],
        "gate_passed": gate_passed,
        "decision": decision,
    }


__all__ = [
    "CandidateFactory",
    "SplitLeakageError",
    "assert_disjoint",
    "encode_frame",
    "group_split",
    "metrics_hash",
    "temporal_split",
    "three_way_split",
    "train",
]
