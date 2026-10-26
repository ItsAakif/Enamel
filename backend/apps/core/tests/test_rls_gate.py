"""The RLS boundary proofs must not be able to disappear silently (F-09, F-07).

``apps/core/tests/test_rls*.py`` need a connection that may ``SET ROLE`` to
the runtime roles, because PostgreSQL always lets a superuser bypass
row-level security and the proofs run against the **real** runtime roles. A
skip is indistinguishable from a pass in a CI exit code, so the release gate
asserts the size of the proofs and the release checklist runs it with
``ENAMEL_REQUIRE_RLS=1``.

The floor lives in ``apps/core/tests/rls_proof_floor.py``, next to the proofs
it protects. This module asserts that:

* every boundary module still exists and declares at least its floor;
* the release gate (``scripts/check_rls.py``) uses **that** number, so the
  gate and the proofs cannot drift apart;
* the total floor is not below the sum of the per-module floors.

This module is deliberately **not** marked ``rls``: it has to run in the
ordinary suite, otherwise the gate would protect nothing.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

from apps.core.tests.rls_proof_floor import (
    BOUNDARY_MODULES,
    STATIC_MINIMUM,
    TOTAL_MINIMUM,
    module_path,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
GATE_SCRIPT_PATH = REPO_ROOT / "scripts" / "check_rls.py"
RLS_MODULE_PATH = module_path("test_rls.py")


def _rls_test_functions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]


def _load_rls_module():
    spec = importlib.util.spec_from_file_location("enamel_rls_under_test", RLS_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_gate_module():
    spec = importlib.util.spec_from_file_location("enamel_check_rls", GATE_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_rls_module_exists() -> None:
    assert RLS_MODULE_PATH.exists(), f"{RLS_MODULE_PATH} is missing"


def test_every_boundary_module_exists() -> None:
    for name in BOUNDARY_MODULES:
        path = module_path(name)
        assert path.exists(), f"the boundary module {name} is missing ({path})"


def test_every_boundary_module_declares_at_least_its_floor() -> None:
    """No boundary module may be emptied while the total still looks fine."""

    for name, floor in BOUNDARY_MODULES.items():
        names = _rls_test_functions(module_path(name))
        assert len(names) >= floor, (
            f"{name} declares only {len(names)} test functions ({names}); "
            f"the floor is {floor}"
        )


def test_the_total_floor_is_not_below_the_static_floors() -> None:
    assert TOTAL_MINIMUM >= STATIC_MINIMUM, (
        f"the gate floor ({TOTAL_MINIMUM}) is below the sum of the per-module "
        f"floors ({STATIC_MINIMUM})"
    )


def test_the_release_gate_uses_the_shared_floor() -> None:
    """The gate and the proofs share one number (F-07)."""

    gate = _load_gate_module()
    assert gate.DEFAULT_MINIMUM == TOTAL_MINIMUM, (
        f"the release gate enforces {gate.DEFAULT_MINIMUM} proofs while the "
        f"shared floor is {TOTAL_MINIMUM}"
    )


def test_the_rls_module_carries_the_rls_marker() -> None:
    module = _load_rls_module()
    markers = {getattr(mark, "name", None) for mark in getattr(module, "pytestmark", [])}
    assert "rls" in markers, (
        "the RLS module lost its 'rls' marker, so 'pytest -m rls' no longer "
        "selects the policy proofs"
    )


def test_the_rls_module_honours_the_require_rls_switch() -> None:
    module = _load_rls_module()
    assert hasattr(module, "require_rls"), "the RLS module lost the require_rls switch"


def test_the_require_rls_switch_follows_the_environment(monkeypatch) -> None:
    module = _load_rls_module()

    monkeypatch.delenv("ENAMEL_REQUIRE_RLS", raising=False)
    assert module.require_rls() is False

    for value in ("1", "true", "TRUE", "yes", "on", " 1 "):
        monkeypatch.setenv("ENAMEL_REQUIRE_RLS", value)
        assert module.require_rls() is True, value

    for value in ("", "0", "false", "no", "off", "maybe"):
        monkeypatch.setenv("ENAMEL_REQUIRE_RLS", value)
        assert module.require_rls() is False, value
