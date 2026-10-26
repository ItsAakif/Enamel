"""Training, activation gate, calibration and reproducibility (T131).

Most assertions read the session-scoped ``trained_bundle`` so the pipeline runs
once; only the hash-stability and forced-rejection cases train again.
"""

from __future__ import annotations

import json

import pytest
from sklearn.dummy import DummyClassifier

from ml.no_show.config import CALIBRATION_BINS, FEATURE_NAMES
from ml.no_show.dataset import build_synthetic_dataset
from ml.no_show.evaluate import DegenerateDatasetError
from ml.no_show.registry import (
    metrics_path,
    read_metrics,
    register_model,
    write_artifact,
    write_metrics,
)
from ml.no_show.train import metrics_hash, train

pytestmark = pytest.mark.django_db

#: The tests train the production configuration (dataset size 1200, seed 42)
#: but skip the temporal fit and permutation importance where they are not
#: under test, so the suite stays fast.
FAST = {
    "dataset_size": 1200,
    "include_temporal": False,
    "include_permutation_importance": False,
}


def test_training_reproduces_an_identical_metrics_hash(tmp_path):
    first = train(seed=42, **FAST)
    second = train(seed=42, **FAST)

    assert first["metrics_hash"] == second["metrics_hash"]
    assert first["metrics"]["candidate"] == second["metrics"]["candidate"]

    path = metrics_path(tmp_path)
    write_metrics(first["metrics"], path)
    stored = read_metrics(path)
    assert stored["metrics_hash"] == first["metrics_hash"]

    # A different seed is a different model and therefore a different hash.
    other = train(seed=7, **FAST)
    assert other["metrics_hash"] != first["metrics_hash"]


def test_metrics_document_carries_the_required_provenance(trained_bundle):
    metrics = trained_bundle["metrics"]

    assert metrics["dataset"]["hash"]
    assert metrics["seed"] == 42
    assert metrics["git_sha"] == "unknown"  # git is unavailable in this workspace
    assert metrics["feature_names"] == list(FEATURE_NAMES)
    assert len(metrics["features"]) == len(FEATURE_NAMES)
    assert all(item["reason"] for item in metrics["features"])
    assert metrics["split"]["patient_overlap"] == 0
    assert metrics["sklearn_version"]
    assert metrics["metrics_hash"] == metrics_hash(metrics)


def test_calibration_bins_are_persisted_and_well_formed(trained_bundle):
    bins = trained_bundle["metrics"]["calibration_bins"]

    assert len(bins) == CALIBRATION_BINS
    assert any(item["count"] > 0 for item in bins)
    for item in bins:
        assert set(item) == {"lower", "upper", "count", "mean_predicted", "observed_rate"}
        assert 0.0 <= item["lower"] < item["upper"] <= 1.0
        if item["count"] > 0:
            assert 0.0 <= item["mean_predicted"] <= 1.0
            assert 0.0 <= item["observed_rate"] <= 1.0


def test_default_candidate_passes_the_activation_gate(trained_bundle):
    result = trained_bundle["result"]
    metrics = trained_bundle["metrics"]

    assert result["gate_passed"] is True
    assert result["decision"] == "activated"
    assert metrics["activation_gate"]["passed"] is True
    assert (
        metrics["candidate"]["roc_auc"]
        >= metrics["baseline"]["logistic_regression"]["roc_auc"]
    )
    assert (
        metrics["candidate"]["brier"]
        <= metrics["baseline"]["logistic_regression"]["brier"]
    )


def test_a_worse_candidate_is_rejected_and_never_activated(tmp_path):
    """Forced-worse case: a constant predictor must never be activated."""

    result = train(
        seed=42,
        dataset_size=1200,
        candidate_factory=lambda seed: DummyClassifier(
            strategy="most_frequent", random_state=seed
        ),
        calibrate=False,
        include_temporal=False,
        include_permutation_importance=False,
    )
    metrics = result["metrics"]

    assert result["gate_passed"] is False
    assert result["decision"] == "rejected"
    gate = metrics["activation_gate"]
    assert gate["passed"] is False
    assert gate["decision"] == "rejected"
    assert (
        metrics["candidate"]["roc_auc"]
        <= metrics["baseline"]["logistic_regression"]["roc_auc"]
    )
    assert (
        metrics["candidate"]["brier"]
        >= metrics["baseline"]["logistic_regression"]["brier"]
    )

    artifact_path = tmp_path / "rejected.joblib"
    artifact_hash = write_artifact(result["artifact"], artifact_path)
    model, _ = register_model(
        metrics,
        artifact_path=str(artifact_path),
        artifact_hash=artifact_hash,
        gate_passed=result["gate_passed"],
    )
    assert model.status == "candidate"
    assert model.metrics_json["activation_gate"]["passed"] is False


def test_registering_the_same_version_twice_is_idempotent(trained_bundle):
    from apps.ml.models import NoShowModel

    metrics = trained_bundle["metrics"]
    kwargs = {
        "artifact_path": trained_bundle["artifact_path"],
        "artifact_hash": "test-artifact-hash",
        "gate_passed": True,
    }
    first, _ = register_model(metrics, **kwargs)
    second, _ = register_model(metrics, **kwargs)

    assert first.pk == second.pk
    assert first.status == "active"
    assert NoShowModel.objects.filter(version=metrics["version"]).count() == 1


def test_all_negative_dataset_aborts_with_an_explicit_error():
    frame = build_synthetic_dataset(42, 1200)
    frame["no_show"] = False

    with pytest.raises(DegenerateDatasetError):
        train(seed=42, dataset=frame, include_temporal=False, include_permutation_importance=False)


def test_metrics_json_is_valid_and_free_of_nan(trained_bundle):
    text = json.dumps(trained_bundle["metrics"], allow_nan=False)

    assert "NaN" not in text
    assert "Infinity" not in text


def test_readme_states_the_synthetic_disclosure_and_no_clinical_claim(settings):
    readme = (settings.BASE_DIR / "ml" / "README.md").read_text(encoding="utf-8").lower()

    assert "synthetic" in readme
    assert "not clinically validated" in readme or "no clinical-validity claim" in readme
    for name in FEATURE_NAMES:
        assert name in readme
