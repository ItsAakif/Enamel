"""Offline ML pipelines (T131).

``ml`` is a plain Python package, not a Django app: the training pipeline must
be runnable from a bare interpreter (``scripts/train_noshow.py``) and must not
import the ORM. The serving side lives in ``apps.ml`` and only consumes the
artifacts written here.
"""

from __future__ import annotations

__all__ = ["no_show"]
