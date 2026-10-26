"""AI evaluation harness (T126).

Public surface: :mod:`runner` (suite orchestration), :mod:`report` (rendering and
schema validation) and :mod:`regression` (baseline comparison).
"""

from __future__ import annotations

from . import regression, report, runner

__all__ = ["regression", "report", "runner"]
