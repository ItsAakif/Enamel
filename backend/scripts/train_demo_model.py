"""Train and register the demo no-show model (rehearsal helper).

The end-to-end workflow "ML inference" reads the active registry row, so the
rehearsal database needs one. It is trained with the shipped pipeline and
registered on the platform path (the global catalogue row, ``clinic_id IS
NULL``), which the runtime role may not write - the training pipeline runs with
the owner, like the other platform operations.

    docker compose -f deploy/docker-compose.yml --profile prod run --rm \
        -e DATABASE_URL=postgresql://enamel:enamel_dev_password@db:5432/enamel \
        api python /app/scripts/train_demo_model.py
"""

import os
import tempfile

from ml.no_show.config import artifact_filename
from ml.no_show.registry import register_model, write_artifact
from ml.no_show.train import train


def main() -> None:
    result = train(seed=42, dataset_size=1200)
    out_dir = tempfile.mkdtemp(prefix="noshow-demo-")
    artifact_path = os.path.join(out_dir, artifact_filename(result["metrics"]["version"]))
    write_artifact(result["artifact"], artifact_path)
    model, _run = register_model(
        result["metrics"],
        artifact_path=artifact_path,
        artifact_hash="demo-artifact",
        gate_passed=True,
        clinic=None,
    )
    print(f"registered no-show model {model.version} (status {model.status})")


if __name__ == "__main__":
    main()
