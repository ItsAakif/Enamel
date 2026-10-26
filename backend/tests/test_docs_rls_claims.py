"""The row-level-security state of the documentation must not regress (F-03, F-08).

``scripts/check_docs.py`` is the only automated protection the documents have.
Before this pass it validated links, retired names, command lines and the
landing claims - but nothing about the *runtime state* of row-level security.
A future edit that put "schema-level only" back into a document would have
passed the gate.

This module proves that the guard fails on every claim the role switch
retired, that a historical statement (a record of the old state) is still
allowed, and that the tree as it stands passes the guard.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_docs.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("enamel_check_docs", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_docs = _load_checker()


def _state_errors(text: str) -> list[str]:
    return check_docs.rls_state_claim_errors(Path("sample.md"), text)


@pytest.mark.parametrize(
    "claim",
    [
        "the row-level-security policies are schema-level only in this stack.",
        "the schema level only state is documented in deploy/README.md",
        "the `enamel_app` role is staged but not enabled (ADR-013)",
        "the policies are not in force at runtime, so the boundary is the app layer",
        "PostgreSQL row-level security is inert in the shipped stack.",
        "the application runs as the superuser, so the policies never apply",
        "the api and worker connect as the compose superuser.",
    ],
)
def test_the_stale_state_claims_are_rejected(claim):
    """Every claim the role switch retired must fail the guard."""

    errors = _state_errors(claim)
    assert errors, f"the documentation guard accepted: {claim}"


def test_a_current_statement_is_accepted():
    text = (
        "The api service connects as the non-superuser role `enamel_app`, so "
        "PostgreSQL's row-level security is in force for it."
    )
    assert not _state_errors(text)


def test_a_historical_statement_is_accepted():
    """A record of the old state is not a claim about the shipped state."""

    text = (
        "The policies were schema-level only before the role switch, and the "
        "application connected as the compose superuser at the time."
    )
    assert not _state_errors(text)


def test_the_proof_count_must_match_the_release_gate():
    floor = check_docs.load_proof_floor()
    path = Path("sample.md")

    wrong = check_docs.rls_proof_count_errors(path, "86 deny-by-default proofs", floor)
    assert wrong, "the guard accepted a proof count the gate does not protect"

    right = check_docs.rls_proof_count_errors(
        path, f"{floor} deny-by-default proofs", floor
    )
    assert not right


# --- C-5: the figures come from one source and cannot be phrased around ------


def _figure_errors(text: str) -> list[str]:
    return check_docs.figure_errors(
        Path("sample.md"),
        text,
        floor=check_docs.load_proof_floor(),
        figures=check_docs.load_figures(),
    )


def test_the_proof_count_is_matched_next_to_any_proof_noun():
    """The number is found by its noun, not by one fixed phrasing (C-5)."""

    floor = check_docs.load_proof_floor()
    for stale in (
        "The deny-by-default matrix (86 proofs) runs against the real roles.",
        f"The deny-by-default matrix ({floor - 1} proofs) runs against them.",
        "the boundary proofs: 86 proofs",
    ):
        assert _figure_errors(stale), f"the guard accepted: {stale}"


def test_the_smoke_total_must_match_the_smoke():
    """A document may not quote a smoke total that the smoke does not run."""

    figures = check_docs.load_figures()
    for stale in (
        "`deploy/smoke.ps1` (9/9 checks) and by",
        f"`deploy/smoke.ps1` ({figures['smoke_checks'] - 1} checks) and by",
        "RESULT: FAIL (2/18)",
    ):
        assert _figure_errors(stale), f"the guard accepted: {stale}"


def test_the_end_to_end_total_must_match_the_run():
    """A document may not quote an end-to-end total that the run does not have."""

    for stale in (
        "Playwright 28/30 - against the switched stack",
        "the end-to-end suite: 20/20",
    ):
        assert _figure_errors(stale), f"the guard accepted: {stale}"


def test_a_test_count_no_suite_runs_is_rejected():
    """A count of tests must be a count some suite runs today (C-5)."""

    for stale in (
        "pytest 1307 passed",
        "the vitest run reports 12 tests",
    ):
        assert _figure_errors(stale), f"the guard accepted: {stale}"


def test_the_documentation_assertion_count_must_match_the_guard():
    """The guard's own assertion count is a figure like the others."""

    assert check_docs.assertion_count_errors(
        Path("sample.md"), "PASS (4 docs, 220 assertions)", 248
    )
    assert not check_docs.assertion_count_errors(
        Path("sample.md"), "PASS (4 docs, 248 assertions)", 248
    )


def test_the_runtime_role_of_the_connection_is_checked():
    """A document may not name a role that is not a runtime role (C-5)."""

    path = Path("sample.md")
    assert check_docs.runtime_role_errors(
        path, "The api service connects as the non-superuser role `enamel_app`."
    ) == []
    assert check_docs.runtime_role_errors(
        path, "The api service connects as `enamel`."
    ), "the guard accepted the owner as the runtime connection"
    assert check_docs.runtime_role_errors(
        path, "The api service connects as the compose owner."
    ), "the guard accepted the owner as the runtime connection"
    assert check_docs.runtime_role_errors(
        path, "The api service connects as a service account."
    ), "the guard accepted an unnamed runtime connection"


def test_the_figures_have_one_source():
    """The proof count is not a second copy: it lives next to the proofs."""

    figures = check_docs.load_figures()
    assert "rls_proofs" not in figures, (
        "the proof count belongs to the floor module next to the proofs"
    )
    assert figures["smoke_checks"] == 20
    assert figures["e2e_tests"] == 30


def test_a_document_must_name_the_runtime_role():
    path = Path("sample.md")
    assert check_docs.rls_role_named_errors(path, "the api runs as `enamel_app`") == []
    assert check_docs.rls_role_named_errors(path, "the api runs as a service account")


def test_every_guarded_file_exists():
    for path in check_docs.RLS_STATE_FILES:
        assert path.exists(), f"the guarded file is missing: {path}"


def test_the_tree_passes_the_documentation_guard():
    """The guard is green on the tree as it stands."""

    assert check_docs.Checker().run() == 0
