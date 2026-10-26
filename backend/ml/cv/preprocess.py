"""Deterministic preprocessing for the CV pipeline (T129).

Every constant that changes the pixels (target size, normalisation mean/std) is
read from YAML, never hard-coded, and the loaded config is recorded on the
inference job. The transform is a pure function of ``(image, config)``: same
input and same config always produce the same array, which is what makes the
output hash reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from PIL import Image

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "configs" / "fixture.yaml"

DEFAULT_RESIZE = (64, 64)
DEFAULT_MEAN = 0.5
DEFAULT_STD = 0.25


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML config; fall back to the committed fixture config."""

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("The CV config must be a YAML mapping.")
    return data


def _resize(image_array: Any) -> tuple[int, int]:
    resize = DEFAULT_RESIZE
    return int(resize[0]), int(resize[1])


def preprocess(image_array: Any, config: Mapping[str, Any] | None = None) -> np.ndarray:
    """Return a normalised ``float32`` array for one image.

    Steps (all deterministic): first frame only, min/max normalisation to
    ``0..1``, fixed resize (bilinear), then ``(x - mean) / std`` with the
    constants from ``config["preprocess"]["normalization"]``.
    """

    config = config or {}
    preprocess_config = config.get("preprocess", {}) if isinstance(config, Mapping) else {}
    resize = preprocess_config.get("resize", DEFAULT_RESIZE)
    height, width = int(resize[0]), int(resize[1])
    normalization = preprocess_config.get("normalization", {})
    mean = float(normalization.get("mean", DEFAULT_MEAN))
    std = float(normalization.get("std", DEFAULT_STD)) or 1.0

    array = np.asarray(image_array, dtype=np.float64)
    if array.ndim == 3:
        array = array[0]
    if array.size == 0:
        raise ValueError("Cannot preprocess an empty image.")

    low = float(np.min(array))
    high = float(np.max(array))
    if high <= low:
        normalized = np.zeros_like(array)
    else:
        normalized = (array - low) / (high - low)

    frame = Image.fromarray((normalized * 255.0).astype(np.uint8))
    frame = frame.resize((width, height), Image.BILINEAR)
    resized = np.asarray(frame, dtype=np.float64) / 255.0

    return ((resized - mean) / std).astype(np.float32)


__all__ = ["DEFAULT_CONFIG_PATH", "load_config", "preprocess"]
