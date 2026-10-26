"""CV pipeline determinism, config recording and fixture provenance (T129)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ml.cv.fixtures import build_dataset, load_dataset
from ml.cv.infer import findings_to_json, output_hash, predict
from ml.cv.preprocess import DEFAULT_CONFIG_PATH, load_config, preprocess

pytestmark = pytest.mark.django_db


def _image() -> np.ndarray:
    ys, xs = np.mgrid[0:32, 0:32]
    radius_sq = (xs - 16) ** 2 + (ys - 16) ** 2
    return (100 + 800 * np.exp(-radius_sq / (2 * 4**2))).astype(np.uint16)


def test_config_is_loaded_from_yaml():
    config = load_config(DEFAULT_CONFIG_PATH)

    assert config["preprocess"]["resize"] == [64, 64]
    assert config["preprocess"]["normalization"] == {"mean": 0.5, "std": 0.25}
    assert config["inference"]["threshold"] == 0.5
    assert config["model"]["version"] == "1.0.0"


def test_preprocess_is_deterministic():
    config = load_config(DEFAULT_CONFIG_PATH)
    image = _image()

    first = preprocess(image, config)
    second = preprocess(image, config)

    assert first.shape == (64, 64)
    assert first.dtype == np.float32
    assert np.array_equal(first, second)


def test_predict_is_deterministic_and_advisory():
    config = load_config(DEFAULT_CONFIG_PATH)
    image = _image()

    first = predict(image, {"instance_id": "x"}, config)
    second = predict(image, {"instance_id": "x"}, config)

    assert first, "the bright blob must produce at least one finding"
    assert findings_to_json(first) == findings_to_json(second)
    assert all(finding.status == "suggested" for finding in first)

    hash_a = output_hash(findings_to_json(first), model_name="m", model_version="1", config=config)
    hash_b = output_hash(findings_to_json(second), model_name="m", model_version="1", config=config)
    assert hash_a == hash_b


def test_output_hash_changes_with_the_config():
    config = load_config(DEFAULT_CONFIG_PATH)
    other = dict(config)
    other["inference"] = {**config["inference"], "threshold": 0.9}
    findings = findings_to_json(predict(_image(), {}, config))

    assert output_hash(findings, model_name="m", model_version="1", config=config) != output_hash(
        findings, model_name="m", model_version="1", config=other
    )


def test_fixture_dataset_is_deterministic_with_provenance(tmp_path):
    first = tmp_path / "a"
    second = tmp_path / "b"
    manifest_a = build_dataset(first)
    build_dataset(second)

    assert (first / "labels.json").read_bytes() == (second / "labels.json").read_bytes()
    for name in manifest_a["images"]:
        assert (first / name).read_bytes() == (second / name).read_bytes()

    dataset = load_dataset(first)
    assert dataset["licence"]
    assert dataset["generator"] == "ml/cv/fixtures.py"
    assert dataset["hash"]
    assert len(dataset["items"]) >= 8
    assert {item["label"] for item in dataset["items"]} == {0, 1}


def test_committed_fixtures_match_the_generator(tmp_path):
    from django.conf import settings

    committed = Path(settings.BASE_DIR) / "fixtures" / "cv"
    generated = tmp_path / "cv"
    manifest = build_dataset(generated)

    assert (committed / "labels.json").read_bytes() == (generated / "labels.json").read_bytes()
    for name in manifest["images"]:
        assert (committed / name).read_bytes() == (generated / name).read_bytes()


def test_evaluation_metrics_hash_is_reproducible(tmp_path):
    import importlib.util
    import json

    from django.conf import settings

    script = Path(settings.BASE_DIR) / "scripts" / "cv_evaluate.py"
    spec = importlib.util.spec_from_file_location("cv_evaluate", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    assert module.main(["--config", str(DEFAULT_CONFIG_PATH), "--out", str(out_a)]) == 0
    assert module.main(["--config", str(DEFAULT_CONFIG_PATH), "--out", str(out_b)]) == 0

    first = json.loads((out_a / "metrics.json").read_text(encoding="utf-8"))
    second = json.loads((out_b / "metrics.json").read_text(encoding="utf-8"))
    assert first["metrics_hash"] == second["metrics_hash"]
    assert first["metrics"]["sensitivity"] is not None
    assert first["metrics"]["specificity"] is not None
    assert first["metrics"]["calibration_bins"]
    assert first["limitations"]

    committed = Path(settings.BASE_DIR) / "artifacts" / "cv" / "metrics.json"
    if committed.exists():
        stored = json.loads(committed.read_text(encoding="utf-8"))
        assert stored["metrics_hash"] == first["metrics_hash"]
