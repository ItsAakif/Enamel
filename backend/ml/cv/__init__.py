"""Deterministic computer-vision pipeline over fixture images (T129).

This package is intentionally dependency-light: numpy, Pillow and scikit-learn
only. There is **no** PyTorch dependency, and the default model is a transparent
intensity/threshold baseline rather than a learned network. Nothing here makes a
clinical claim; the fixture evaluation documents its own limitations.
"""

from .infer import Finding, findings_to_json, output_hash, predict
from .preprocess import load_config, preprocess

__all__ = [
    "Finding",
    "findings_to_json",
    "load_config",
    "output_hash",
    "predict",
    "preprocess",
]
