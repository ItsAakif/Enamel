"""Artifact and metrics persistence plus the versioned model registry (T131).

Two halves:

* **Files** -- ``write_artifact``/``load_artifact`` (joblib) and
  ``write_metrics``/``read_metrics`` (deterministic JSON). Loading an artifact
  written by a different scikit-learn version fails loudly instead of producing
  silently different probabilities.
* **Registry rows** -- ``register_model`` writes the ``NoShowModel`` and
  ``TrainingRun`` rows and applies the activation gate; ``activate``/``retire``
  are status changes (never in-place edits of metrics). Django is imported
  lazily so the training pipeline stays importable without a settings module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn

from .config import (
    METRIC_PRECISION,
    METRICS_FILENAME,
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_RETIRED,
)


class ArtifactVersionMismatch(RuntimeError):
    """Raised when an artifact was trained under a different scikit-learn."""


def file_hash(path: str | Path) -> str:
    """Return the SHA-256 of a file."""

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact(artifact: dict[str, Any], path: str | Path) -> str:
    """Persist ``artifact`` with joblib and return the artifact hash."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, target)
    return file_hash(target)


def load_artifact(path: str | Path) -> dict[str, Any]:
    """Load an artifact, refusing a scikit-learn version mismatch."""

    artifact = joblib.load(Path(path))
    if not isinstance(artifact, dict) or "model" not in artifact:
        raise ValueError("artifact is not a no-show model bundle")
    recorded = str(artifact.get("sklearn_version", ""))
    if recorded != sklearn.__version__:
        raise ArtifactVersionMismatch(
            "artifact was trained with scikit-learn "
            f"{recorded or 'unknown'} but {sklearn.__version__} is installed; "
            "retrain instead of loading"
        )
    return artifact


def predict_probabilities(artifact: dict[str, Any], matrix: np.ndarray) -> np.ndarray:
    """Return positive-class probabilities for an encoded feature matrix."""

    model = artifact["model"]
    return np.asarray(model.predict_proba(matrix), dtype=float)[:, 1]


def write_metrics(metrics: dict[str, Any], path: str | Path) -> str:
    """Write the metrics document as deterministic JSON; return its hash."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(metrics, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_metrics(path: str | Path) -> dict[str, Any]:
    """Read a metrics document."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def metrics_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / METRICS_FILENAME


# --------------------------------------------------------------------------- #
# Registry rows (Django)
# --------------------------------------------------------------------------- #


def register_model(
    metrics: dict[str, Any],
    *,
    artifact_path: str,
    artifact_hash: str,
    gate_passed: bool,
    clinic: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """Write ``NoShowModel`` + ``TrainingRun`` and apply the activation gate.

    When the gate passes the new row becomes ``active`` and any previously
    active row in the same scope is retired. When it fails the new row is kept
    as ``candidate`` (never activated) so the rejection is visible.

    A **clinic-scoped** model is written inside that clinic's scope, like
    every other tenant row (ADR-013). The **global** catalogue row
    (``clinic_id IS NULL``) is a platform row: it is written by the training
    pipeline on the platform path - the maintenance role or the owner, which
    hold the bypass - and the runtime role is refused by the database.
    """



    if clinic is not None:
        from apps.core.tenancy import with_clinic

        with with_clinic(clinic):
            return _register_model(
                metrics,
                artifact_path=artifact_path,
                artifact_hash=artifact_hash,
                gate_passed=gate_passed,
                clinic=clinic,
                params=params,
            )
    return _register_model(
        metrics,
        artifact_path=artifact_path,
        artifact_hash=artifact_hash,
        gate_passed=gate_passed,
        clinic=clinic,
        params=params,
    )


def _register_model(
    metrics: dict[str, Any],
    *,
    artifact_path: str,
    artifact_hash: str,
    gate_passed: bool,
    clinic: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    from django.db import transaction

    from apps.ml.models import NoShowModel, TrainingRun

    status = STATUS_ACTIVE if gate_passed else STATUS_CANDIDATE
    trained_at = metrics.get("trained_at")

    with transaction.atomic():
        if gate_passed:
            previous = NoShowModel.objects.filter(
                status=STATUS_ACTIVE, clinic=clinic
            )
            if clinic is None:
                previous = NoShowModel.objects.filter(
                    status=STATUS_ACTIVE, clinic__isnull=True
                )
            previous.exclude(version=metrics["version"]).update(status=STATUS_RETIRED)

        # ``version`` already encodes the seed and the dataset hash, so a
        # re-run of the same pipeline is the same model: it updates the row
        # instead of colliding on the unique version. A different dataset or
        # seed is a different version and therefore a new row (a status change
        # is never an in-place metric edit).
        model, _created = NoShowModel.objects.update_or_create(
            version=metrics["version"],
            defaults={
                "clinic": clinic,
                "algorithm": metrics.get("algorithm", ""),
                "features": metrics.get("feature_names", []),
                "dataset_hash": metrics["dataset"]["hash"],
                "threshold": metrics["threshold"],
                "artifact_path": artifact_path,
                "artifact_hash": artifact_hash,
                "metrics_json": metrics,
                "metrics_hash": metrics["metrics_hash"],
                "status": status,
                "is_synthetic": True,
                "sklearn_version": metrics.get("sklearn_version", ""),
                "trained_at": trained_at,
            },
        )
        run = TrainingRun.objects.create(
            model=model,
            clinic=clinic,
            seed=metrics["seed"],
            dataset_hash=metrics["dataset"]["hash"],
            metrics_json=metrics,
            metrics_hash=metrics["metrics_hash"],
            artifact_hash=artifact_hash,
            git_sha=metrics.get("git_sha", "unknown"),
            params=params or {},
        )
    return model, run


def active_model(*, clinic: Any = None) -> Any:
    """Return the active model for a scope (clinic row first, then global)."""

    from apps.ml.models import NoShowModel

    if clinic is not None:
        scoped = NoShowModel.objects.filter(
            status=STATUS_ACTIVE, clinic=clinic
        ).first()
        if scoped is not None:
            return scoped
    return NoShowModel.objects.filter(status=STATUS_ACTIVE, clinic__isnull=True).first()


def set_status(model: Any, status: str) -> Any:
    """Change a model's status in place (the only mutable field)."""

    if status not in {STATUS_CANDIDATE, STATUS_ACTIVE, STATUS_RETIRED}:
        raise ValueError(f"unknown status {status!r}")
    model.status = status
    model.save(update_fields=["status", "updated_at"])
    return model


def activate(model: Any) -> Any:
    return set_status(model, STATUS_ACTIVE)


def retire(model: Any) -> Any:
    return set_status(model, STATUS_RETIRED)


def round_probability(value: float) -> float:
    """Round a probability for the API surface."""

    return round(float(value), METRIC_PRECISION)


__all__ = [
    "ArtifactVersionMismatch",
    "active_model",
    "activate",
    "file_hash",
    "load_artifact",
    "metrics_path",
    "predict_probabilities",
    "read_metrics",
    "register_model",
    "retire",
    "round_probability",
    "set_status",
    "write_artifact",
    "write_metrics",
]
