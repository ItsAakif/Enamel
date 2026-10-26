"""Train the no-show model and write its artifact + metrics document (T131).

Usage::

    python scripts/train_noshow.py --dataset synthetic --seed 42 --out artifacts/ml
    python scripts/train_noshow.py --dataset synthetic --seed 42 --out artifacts/ml --check

``--check`` re-runs the pipeline and compares the recomputed metrics hash with
the one in the stored ``noshow_metrics.json``. It writes nothing and exits 0
only when the hashes are identical, so a non-reproducible pipeline fails CI.

The model is trained on **synthetic** data. The metrics document and the
inference API both disclose this; nothing here is a clinical claim.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


def _git_sha() -> str:
    """Best-effort git SHA; ``unknown`` when git is unavailable or absent."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = (result.stdout or "").strip()
    return sha or "unknown"


def _setup_django() -> bool:
    """Configure Django when a database is reachable; return whether it is."""

    try:
        import django

        django.setup()
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[warn] Django is not configured ({exc}); registry rows skipped.")
        return False
    return True


def _resolve_out(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path


def _register(metrics, artifact_path: Path, artifact_hash: str, gate_passed: bool) -> None:
    from ml.no_show.registry import register_model

    model, run = register_model(
        metrics,
        artifact_path=str(artifact_path),
        artifact_hash=artifact_hash,
        gate_passed=gate_passed,
        clinic=None,
        params={"dataset": metrics["dataset"]["name"], "seed": metrics["seed"]},
    )
    print(
        f"[registry] NoShowModel {model.version} status={model.status} "
        f"run={run.id}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the no-show model.")
    parser.add_argument("--dataset", default="synthetic", choices=["synthetic"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="artifacts/ml")
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Re-run and compare the metrics hash; write nothing.",
    )
    options = parser.parse_args(argv)

    from ml.no_show import config
    from ml.no_show.config import artifact_filename
    from ml.no_show.dataset import build_synthetic_dataset, dataset_summary
    from ml.no_show.registry import (
        file_hash,
        metrics_path,
        read_metrics,
        write_artifact,
        write_metrics,
    )
    from ml.no_show.train import train

    out_dir = _resolve_out(options.out)
    size = options.size or config.DEFAULT_DATASET_SIZE
    git_sha = _git_sha()

    frame = build_synthetic_dataset(options.seed, size)
    summary = dataset_summary(frame)
    result = train(
        seed=options.seed,
        dataset=frame,
        git_sha=git_sha,
    )
    metrics = result["metrics"]
    artifact = result["artifact"]

    print(
        f"dataset={summary['name']} n={summary['n']} "
        f"patients={summary['patients']} no_show_rate={summary['no_show_rate']}"
    )
    print(f"dataset_hash={summary['hash']}")
    print(f"version={metrics['version']} threshold={metrics['threshold']}")
    print(
        "holdout: candidate roc_auc="
        f"{metrics['candidate']['roc_auc']} brier={metrics['candidate']['brier']} | "
        "baseline(logistic) roc_auc="
        f"{metrics['baseline']['logistic_regression']['roc_auc']} "
        f"brier={metrics['baseline']['logistic_regression']['brier']}"
    )
    print(f"activation_gate={metrics['activation_gate']['decision']}")
    print(f"metrics_hash={metrics['metrics_hash']}")

    if options.check:
        stored_file = metrics_path(out_dir)
        if not stored_file.exists():
            print(f"[check] FAIL: no stored metrics at {stored_file}")
            return 1
        stored = read_metrics(stored_file)
        stored_hash = stored.get("metrics_hash")
        if stored_hash != metrics["metrics_hash"]:
            print(
                "[check] FAIL: metrics hash changed "
                f"(stored={stored_hash} recomputed={metrics['metrics_hash']})"
            )
            return 1
        print("[check] OK: metrics hash reproduced exactly")
        return 0

    artifact_file = out_dir / artifact_filename(metrics["version"])
    artifact_hash = write_artifact(artifact, artifact_file)
    write_metrics(metrics, metrics_path(out_dir))
    print(f"artifact={artifact_file} sha256={artifact_hash}")
    print(f"metrics={metrics_path(out_dir)}")

    if _setup_django():
        try:
            _register(metrics, artifact_file, artifact_hash, result["gate_passed"])
        except Exception as exc:  # pragma: no cover - database dependent
            print(f"[warn] could not write registry rows: {exc}")
        else:
            # Sanity: the artifact we just wrote must load under this sklearn.
            from ml.no_show.registry import load_artifact

            assert file_hash(artifact_file) == artifact_hash
            load_artifact(artifact_file)
            print("[registry] artifact reloads under the pinned scikit-learn")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
