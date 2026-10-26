#!/usr/bin/env python
"""Release gate for the row-level-security proofs (F-09, F-07).

The boundary proof modules (``backend/apps/core/tests/test_rls*.py``) need a
connection that may ``SET ROLE`` to the runtime roles: PostgreSQL always lets
a superuser bypass row-level security, so the proofs switch the connection to
the **real** runtime roles (``enamel_app``, ``enamel_worker``, the patient
roles) and assert the role they ran under. Without such a connection the
modules *fail* (they never skip), but the gate still has to hold the line:

1. it runs ``pytest -m rls`` and counts the tests that were actually
   collected and executed;
2. it fails when fewer than ``--minimum`` RLS tests ran, which is what a
   silent skip looks like;
3. it fails when the run reported skips;
4. it sets ``ENAMEL_REQUIRE_RLS=1`` so the modules turn their own skip into a
   hard failure.

The floor itself is **not** written here: it lives in
``backend/apps/core/tests/rls_proof_floor.py``, next to the proofs it
protects, and the documents claim the same number (the documentation guard in
``scripts/check_docs.py`` checks that too). The number can therefore not
drift away from the proofs it describes.

Usage::

    python scripts/check_rls.py
    python scripts/check_rls.py --minimum 10
    python scripts/check_rls.py --backend .venv/Scripts/python.exe

Exit code 0 means the gate passed; 1 means it failed.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

#: The module that holds the floor of the proofs.
PROOF_FLOOR_PATH = BACKEND / "apps" / "core" / "tests" / "rls_proof_floor.py"


def _load_proof_floor():
    """Load the shared proof floor (the single source of the number)."""

    spec = importlib.util.spec_from_file_location(
        "enamel_rls_proof_floor", PROOF_FLOOR_PATH
    )
    if spec is None or spec.loader is None:  # pragma: no cover - import guard
        raise RuntimeError(f"the proof floor module is missing: {PROOF_FLOOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The RLS policy proofs the release gate expects to see executed.
DEFAULT_MINIMUM = _load_proof_floor().TOTAL_MINIMUM

SUMMARY_RE = re.compile(r"^(?P<count>\d+)\s+(?P<word>passed|failed|skipped|error|errors)", re.M)


def _find_python(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        BACKEND / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _parse_summary(output: str) -> dict[str, int]:
    """Extract the per-outcome counts from the pytest summary line."""

    counts: dict[str, int] = {}
    for line in output.splitlines():
        stripped = line.strip()
        match = re.match(r"^(\d+)\s+(passed|failed|skipped|xfailed|xpassed|error|errors|warnings?)\b", stripped)
        if match:
            outcome = match.group(2)
            if outcome == "error":
                outcome = "errors"
            counts[outcome] = counts.get(outcome, 0) + int(match.group(1))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minimum",
        type=int,
        default=DEFAULT_MINIMUM,
        help=f"minimum number of RLS tests that must run (default {DEFAULT_MINIMUM})",
    )
    parser.add_argument(
        "--python",
        dest="python",
        default=None,
        help="path to the interpreter that runs pytest",
    )
    parser.add_argument(
        "--backend",
        default=str(BACKEND),
        help="path to the backend directory (default <repo>/backend)",
    )
    args = parser.parse_args(argv)

    backend = Path(args.backend).resolve()
    if not (backend / "manage.py").exists():
        print(f"check_rls FAILED - {backend} is not the backend directory", file=sys.stderr)
        return 1

    python = _find_python(args.python)
    env = dict(os.environ)
    # The module turns its own skip into a hard failure with this switch.
    env["ENAMEL_REQUIRE_RLS"] = "1"

    command = [
        python,
        "-m",
        "pytest",
        "-m",
        "rls",
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    print(f"check_rls: {' '.join(command)} (cwd={backend})", flush=True)
    completed = subprocess.run(
        command,
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    print(output.rstrip())

    if completed.returncode != 0:
        print(
            "check_rls FAILED - the RLS gate run did not pass "
            f"(exit code {completed.returncode})",
            file=sys.stderr,
        )
        return 1

    counts = _parse_summary(output)
    passed = counts.get("passed", 0)
    skipped = counts.get("skipped", 0)

    if passed < args.minimum:
        print(
            f"check_rls FAILED - only {passed} RLS test(s) ran, the gate requires "
            f"at least {args.minimum}. A silent skip looks exactly like this.",
            file=sys.stderr,
        )
        return 1

    if skipped:
        print(
            f"check_rls FAILED - {skipped} RLS test(s) were skipped; the gate "
            "requires every RLS proof to run.",
            file=sys.stderr,
        )
        return 1

    print(f"check_rls OK - {passed} RLS policy proofs ran (minimum {args.minimum}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
