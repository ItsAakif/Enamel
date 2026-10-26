"""Transparent baseline inference for the CV pipeline (T129).

The default model is an **intensity/threshold heuristic**, not a trained network:
it thresholds the normalised image, groups connected pixels and reports each
region as a ``Finding``. It exists so the whole job lifecycle (preprocess ->
predict -> persist -> hash) is exercised end to end without pretending to be a
diagnostic model.

Findings are advisory: every finding carries ``status="suggested"`` and is never
written to the chart automatically.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from .preprocess import load_config, preprocess

SUGGESTED = "suggested"


@dataclass(frozen=True)
class Finding:
    """One advisory region of interest."""

    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x, y, width, height (preprocessed grid)
    status: str = SUGGESTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(float(self.confidence), 6),
            "bbox": [int(value) for value in self.bbox],
            "status": self.status,
        }


def _confidence(mean_intensity: float) -> float:
    """Map a mean normalised intensity to a 0..1 confidence (deterministic)."""

    return float(1.0 / (1.0 + np.exp(-float(mean_intensity))))


def predict(
    image_array: Any,
    meta: Mapping[str, Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[Finding]:
    """Return the advisory findings for one image.

    ``meta`` is accepted for interface compatibility (patient/instance context);
    the baseline ignores it so a prediction depends only on pixels and config.
    """

    config = config or load_config()
    inference = config.get("inference", {}) if isinstance(config, Mapping) else {}
    threshold = float(inference.get("threshold", 0.5))
    label = str(inference.get("label", "finding"))
    min_area = int(inference.get("min_area_px", 4))
    min_confidence = float(inference.get("min_confidence", 0.5))

    grid = preprocess(image_array, config)
    mask = grid > threshold
    if not mask.any():
        return []

    labels, count = ndimage.label(mask)
    objects = ndimage.find_objects(labels)
    findings: list[Finding] = []
    for index in range(1, count + 1):
        region = labels == index
        area = int(region.sum())
        if area < min_area:
            continue
        slices = objects[index - 1]
        y_slice, x_slice = slices
        bbox = (
            int(x_slice.start),
            int(y_slice.start),
            int(x_slice.stop - x_slice.start),
            int(y_slice.stop - y_slice.start),
        )
        confidence = _confidence(float(grid[region].mean()))
        if confidence < min_confidence:
            continue
        findings.append(Finding(label=label, confidence=confidence, bbox=bbox))

    findings.sort(key=lambda item: (-item.confidence, item.bbox))
    return findings


def findings_to_json(findings: list[Finding]) -> list[dict[str, Any]]:
    return [finding.as_dict() for finding in findings]


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def output_hash(
    findings_payload: Any,
    *,
    model_name: str,
    model_version: str,
    config: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 over the canonical ``(config, model, findings)`` document."""

    document = {
        "config": config or {},
        "model": {"name": model_name, "version": model_version},
        "findings": findings_payload,
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


__all__ = [
    "SUGGESTED",
    "Finding",
    "canonical_json",
    "findings_to_json",
    "output_hash",
    "predict",
]
