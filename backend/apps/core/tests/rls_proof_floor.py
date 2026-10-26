"""The floor of the row-level-security boundary proofs (F-07).

The release gate (``scripts/check_rls.py``) runs ``pytest -m rls`` and fails
when fewer proofs ran than the floor, or when any proof was skipped. This
module is the **single place** where the floor lives, so the number that the
gate protects and the number the documents claim cannot drift apart:

* ``scripts/check_rls.py`` reads ``TOTAL_MINIMUM`` from here (it is the
  default of ``--minimum``);
* ``apps/core/tests/test_rls_gate.py`` asserts that every boundary module
  still declares at least its floor, and that the gate and the documents use
  this very number;
* ``scripts/check_docs.py`` asserts that the documents claim this number.

Two numbers live here, and they measure different things:

``TOTAL_MINIMUM``
    the number of proofs that must **run** - the gate's floor. It is the
    collected count of ``pytest -m rls`` (the boundary modules are
    parametrised, so it is larger than the number of test functions).

``BOUNDARY_MODULES``
    the number of test **functions** each boundary module must keep. It is
    the per-module floor the gate test asserts, so a module cannot be
    emptied while the total still looks healthy.

The floor is a **minimum**, not an exact count: adding a proof does not
require a change here, removing one does (the gate fails, and the documents
have to be brought back in line with the floor).
"""

from __future__ import annotations

from pathlib import Path

#: The boundary modules and the number of test functions each one must keep.
BOUNDARY_MODULES: dict[str, int] = {
    "test_rls.py": 32,
    "test_rls_context.py": 13,
    "test_rls_roles.py": 11,
    "test_rls_runtime_paths.py": 13,
    "test_rls_ops_paths.py": 6,
    "test_rls_test_role.py": 7,
    "test_rls_write_paths.py": 5,
}

#: The floor of the whole gate: the number of proofs that must run.
#: It is the collected count of ``pytest -m rls`` over the boundary modules.
TOTAL_MINIMUM: int = 101

#: The sum of the per-module floors (the number of test functions).
STATIC_MINIMUM: int = sum(BOUNDARY_MODULES.values())

#: Where the boundary modules live.
MODULES_DIR = Path(__file__).resolve().parent


def module_path(name: str) -> Path:
    """The path of one boundary module."""

    return MODULES_DIR / name


__all__ = [
    "BOUNDARY_MODULES",
    "MODULES_DIR",
    "STATIC_MINIMUM",
    "TOTAL_MINIMUM",
    "module_path",
]
