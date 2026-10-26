"""No-show prediction pipeline (conventional ML, no deep learning).

Modules
-------
``config``     fixed knobs (seed, sizes, feature order, metric definitions)
``features``   the documented, booking-time-only feature registry
``dataset``    synthetic dataset builder + dataset hashing
``evaluate``   metric, calibration and subgroup computation
``train``      leakage-safe split, baseline, candidate, activation gate
``registry``   artifact/metrics persistence and the versioned model registry

The pipeline is deterministic: a fixed seed and dataset hash produce a
byte-stable metrics document (see ``train.metrics_hash``). There is no
clinical-validity claim anywhere: the shipped model is trained on synthetic
data and is disclosed as such.
"""

from __future__ import annotations

__all__ = [
    "config",
    "dataset",
    "evaluate",
    "features",
    "registry",
    "train",
]
