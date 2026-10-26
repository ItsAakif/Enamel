"""The public landing copy must not make claims the product cannot back (F-01).

``scripts/check_claims.py`` is the CI guard; this module proves that the guard
actually fails on the claims the first implementation shipped, and that the
required limitation sentence is still publishable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO_ROOT / "scripts" / "check_claims.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("enamel_check_claims", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_claims = _load_checker()


def _errors_for(source: str, tmp_path: Path) -> list[str]:
    target = tmp_path / "LandingSnippet.tsx"
    target.write_text(source, encoding="utf-8")
    return check_claims.check_source(target)


@pytest.mark.parametrize(
    "claim",
    [
        "Our neural network was trained on over 40 million annotated dental records.",
        "Enamel integrates clinical-grade AI into your workflow.",
        "Populates your EMR in real-time with 99.8% medical terminology accuracy.",
        "Our proprietary Clinical Alpha engine spots pathology months before it shows.",
        "3 Pathologies Detected",
        "40% Less Wait Time",
        "98% First-Pass Rate",
        "-3 days Avg. Collection Cycle",
        "Trusted by DENTSPLY SIRONA, MAYO CLINIC, HARVARD MEDICAL and STANFORD DENTAL",
        "ORAL-B ACADEMY",
    ],
)
def test_forbidden_claims_are_rejected(claim, tmp_path):
    errors = _errors_for(f"export default function X() {{ return <p>{claim}</p>; }}\n", tmp_path)

    assert errors, f"the claims check did not reject: {claim}"


def test_the_required_limitation_sentence_is_allowed(tmp_path):
    source = (
        "export default function X() {\n"
        "  return (\n"
        "    <p>\n"
        "      This is a fixture-only evaluation. It is not clinically validated, and it\n"
        "      makes no diagnostic claim.\n"
        "    </p>\n"
        "  );\n"
        "}\n"
    )

    assert _errors_for(source, tmp_path) == []


def test_css_gradients_are_not_treated_as_copy(tmp_path):
    source = (
        "export default function X() {\n"
        "  return (\n"
        "    <div\n"
        "      className=\"depth-blob w-[600px] h-[300px] top-0 left-[-100px] opacity-20\"\n"
        '      style={{ background: "radial-gradient(ellipse, rgba(70,71,211,0.15) 0%, transparent 70%)" }}\n'
        "    />\n"
        "  );\n"
        "}\n"
    )

    assert _errors_for(source, tmp_path) == []


def test_the_shipped_landing_sources_pass():
    checks, errors = check_claims.run()

    assert checks > 0, "the claims check found no landing source to scan"
    assert errors == []


def test_every_public_source_is_scanned():
    """Hole (a): the scanned set covers every route reachable without a session.

    The public navbar and the public pages are public surfaces: a claim on a
    login screen is a claim. The set is explicit so that a new public page
    cannot be added without a decision about it.
    """

    expected = {
        "artifacts/enamel-landing/src/components/ResizableNavbar.tsx",
        "artifacts/enamel-landing/src/pages/LoginPage.tsx",
        "artifacts/enamel-landing/src/pages/RegisterPage.tsx",
        "artifacts/enamel-landing/src/pages/ResetPasswordPage.tsx",
        "artifacts/enamel-landing/src/pages/PublicBookingPage.tsx",
        "artifacts/enamel-landing/src/pages/portal/PortalLoginPage.tsx",
    }
    scanned = {
        path.relative_to(REPO_ROOT).as_posix() for path in check_claims.landing_sources()
    }

    assert expected <= scanned, f"not scanned: {sorted(expected - scanned)}"
    assert check_claims.missing_public_sources() == []


def test_a_negation_in_an_earlier_clause_does_not_excuse_a_claim(tmp_path):
    """Hole (b): the negation has to apply to the phrase it precedes.

    "Not a mock: 99.8% accuracy on real patients" is a claim; the negation
    belongs to the clause before the colon.
    """

    source = (
        "export default function X() {\n"
        "  return <p>Not a mock: 99.8% accuracy on real patients</p>;\n"
        "}\n"
    )

    errors = _errors_for(source, tmp_path)

    assert errors, "the claims check excused a claim behind an unrelated negation"


def test_a_claim_in_a_user_visible_attribute_is_rejected(tmp_path):
    """Hole (c): alt, title, aria-label and placeholder are user-visible copy."""

    for attribute, value in (
        ("alt", "99.8% accuracy on real patients"),
        ("title", "40% Less Wait Time"),
        ("aria-label", "clinical-grade AI"),
        ("placeholder", "Trusted by leading clinics"),
    ):
        source = f'export default function X() {{ return <input {attribute}="{value}" />; }}\n'

        errors = _errors_for(source, tmp_path)

        assert errors, f"the claims check did not reject the {attribute} attribute"


@pytest.mark.parametrize(
    "claim",
    [
        "Trusted by leading dental practices across India",
        "Endorsed by practitioners who know it best",
        "In partnership with the leading clinics",
        "As seen in the leading dental press",
        "Used by clinics across India",
    ],
)
def test_endorsement_framing_is_rejected(claim, tmp_path):
    """Hole (d): an endorsement needs no named institution to be a claim."""

    errors = _errors_for(f"export default function X() {{ return <p>{claim}</p>; }}\n", tmp_path)

    assert errors, f"the claims check did not reject: {claim}"
