#!/usr/bin/env python
"""Guard the public copy against unsupported product claims (F-01, N-08).

The public marketing page is served at ``/`` by the production ``web`` service.
A review of the first implementation found that it advertised claims the
shipped code cannot support: a "neural network trained on over 40 million
annotated dental records", a "proprietary Clinical Alpha engine", "clinical-grade
AI", "99.8% medical terminology accuracy", "-3 days Avg. Collection Cycle", a
"3 Pathologies Detected" overlay, an endorsement strip naming DENTSPLY SIRONA,
MAYO CLINIC, HARVARD MEDICAL, STANFORD DENTAL and ORAL-B ACADEMY, and
testimonials attributed to named people and clinics.

This module fails when any of that comes back. It is deliberately a *text*
check: it scans the public sources, not the rendered page, so it runs in CI
without a browser.

What is scanned
---------------

Every source that renders on a route reachable **without a session**: the
landing components, the legal pages, the landing page, the public navbar and
the public pages (login, register, password reset, invitation acceptance,
public booking, portal sign-in). A claim on a public screen is a claim.

What is not copy
----------------

Class names, inline styles, animation configuration, gradient constants,
imports and comments are code, not copy. They are stripped before the scan.
The values of ``alt``, ``title``, ``aria-label`` and ``placeholder`` are
*not* code: they are user-visible copy, so they are extracted from the source
and checked as copy in their own right.

Negation
--------

A claim is allowed when it is explicitly negated, because the required
limitation sentence ("this is a fixture-only evaluation, it is not clinically
validated, and it makes no diagnostic claim") has to be publishable. A
negation only excuses a claim when it applies to *that* phrase: the negation
word has to sit in the short window immediately before the match, in the same
sentence and in the same clause. A negation that belongs to an earlier clause
("Not a mock: 99.8% accuracy on real patients") does not excuse the claim
that follows it.

    python scripts/check_claims.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The public landing sources. Anything rendered on ``/`` belongs here.
LANDING_DIR = ROOT / "artifacts" / "enamel-landing" / "src" / "components" / "landing"
LEGAL_DIR = ROOT / "artifacts" / "enamel-landing" / "src" / "pages" / "legal"
PAGES_DIR = ROOT / "artifacts" / "enamel-landing" / "src" / "pages"
COMPONENTS_DIR = ROOT / "artifacts" / "enamel-landing" / "src" / "components"

#: Every remaining source that renders on a route reachable without a
#: session. The set is explicit so that a new public page cannot be added
#: without a decision about whether it is scanned.
PUBLIC_SOURCES: tuple[Path, ...] = (
    PAGES_DIR / "LandingPage.tsx",
    COMPONENTS_DIR / "ResizableNavbar.tsx",
    PAGES_DIR / "LoginPage.tsx",
    PAGES_DIR / "RegisterPage.tsx",
    PAGES_DIR / "ResetPasswordPage.tsx",
    PAGES_DIR / "AcceptInvitePage.tsx",
    PAGES_DIR / "PublicBookingPage.tsx",
    PAGES_DIR / "GuidePage.tsx",
    PAGES_DIR / "portal" / "PortalLoginPage.tsx",
)

SOURCE_SUFFIXES = (".tsx", ".ts")

# --- Forbidden claim patterns ------------------------------------------------

#: A percentage in visible copy. Any measured rate published without a
#: measurement in this repository is a claim the product cannot back.
PERCENTAGE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*%")

#: "40 million annotated dental records" and friends.
MILLION_RE = re.compile(r"\b\d[\d,.]*\s*(?:million|billion)\b", re.IGNORECASE)
RECORDS_RE = re.compile(r"\b(?:million|billion)\b[^.]{0,40}\brecords?\b", re.IGNORECASE)

#: Named technology / marketing phrases.
PHRASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("clinical-grade", re.compile(r"clinical[-\s]?grade", re.IGNORECASE)),
    ("neural network", re.compile(r"neural\s+network", re.IGNORECASE)),
    ("AI detected", re.compile(r"\bAI\s+detected\b", re.IGNORECASE)),
    ("trained network", re.compile(r"\btrained\s+(?:neural\s+)?network\b", re.IGNORECASE)),
    ("clinical alpha", re.compile(r"clinical\s+alpha", re.IGNORECASE)),
    ("months before it is visible", re.compile(r"months\s+before", re.IGNORECASE)),
    ("first-pass rate", re.compile(r"first[-\s]?pass\s+rate", re.IGNORECASE)),
    (
        "duration claim",
        re.compile(
            r"(?<![\w-])-\s*\d+(?:[.,]\d+)?\s*(?:day|week|month|hour|minute)s?\b"
            r"|\b\d+\s*(?:day|week|month|hour|minute)s?\s+"
            r"(?:faster|quicker|shorter|sooner|earlier|less|lower|reduction|cycle|"
            r"turnaround|improvement|save|saved)\b",
            re.IGNORECASE,
        ),
    ),
    ("diagnostic claim", re.compile(r"\bdiagnos\w*", re.IGNORECASE)),
    ("pathology claim", re.compile(r"\bpatholog\w*", re.IGNORECASE)),
    ("caries claim", re.compile(r"\bcaries\b", re.IGNORECASE)),
    ("bone loss claim", re.compile(r"\bbone\s+loss\b", re.IGNORECASE)),
    (
        "accuracy claim",
        re.compile(
            r"\b\d+(?:[.,]\d+)?\s*%[^.]{0,40}\baccurac"
            r"|\baccuracy\s+(?:claim|rating|figure|score)\b",
            re.IGNORECASE,
        ),
    ),
    ("clinically validated", re.compile(r"\bclinically\s+validated\b", re.IGNORECASE)),
    ("clinically proven", re.compile(r"\bclinically\s+proven\b", re.IGNORECASE)),
    (
        "regulatory clearance",
        re.compile(r"\b(?:FDA|NMPA|CE|MDR)[-\s]?(?:cleared|approved|marked|certified)\b", re.IGNORECASE),
    ),
    ("hipaa compliant", re.compile(r"\bHIPAA[-\s]compliant\b", re.IGNORECASE)),
    ("hipaa certified", re.compile(r"\bHIPAA[-\s]certified\b", re.IGNORECASE)),
)

#: Third-party organisations that must never appear as an endorsement.
INSTITUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DENTSPLY", re.compile(r"dentsply", re.IGNORECASE)),
    ("SIRONA", re.compile(r"sirona", re.IGNORECASE)),
    ("MAYO CLINIC", re.compile(r"mayo\s+clinic", re.IGNORECASE)),
    ("HARVARD", re.compile(r"harvard", re.IGNORECASE)),
    ("STANFORD", re.compile(r"stanford", re.IGNORECASE)),
    ("ORAL-B", re.compile(r"oral[-\s]?b\b", re.IGNORECASE)),
    ("COLGATE", re.compile(r"colgate", re.IGNORECASE)),
    ("3M DENTAL", re.compile(r"\b3m\s+dental", re.IGNORECASE)),
)

#: Endorsement *framing*: a claim of adoption that names no institution but
#: borrows the grammar of one ("trusted by leading dental practices"). A
#: named institution is not the only way to imply an endorsement.
ENDORSEMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("trusted by", re.compile(r"\btrusted\s+by\b", re.IGNORECASE)),
    ("endorsed by", re.compile(r"\bendorsed\s+by\b", re.IGNORECASE)),
    ("in partnership with", re.compile(r"\bin\s+partnership\s+with\b", re.IGNORECASE)),
    ("as seen in", re.compile(r"\bas\s+seen\s+in\b", re.IGNORECASE)),
    (
        "used by clinics",
        re.compile(r"\bused\s+by\s+(?:dental\s+)?(?:clinics|practices)\b", re.IGNORECASE),
    ),
)

# --- Negation handling -------------------------------------------------------

NEGATION_RE = re.compile(
    r"\b(?:no|not|never|nothing|none|neither|nor|without|cannot|can't|"
    r"doesn't|don't|isn't|aren't|won't|must\s+not|may\s+not|should\s+not)\b",
    re.IGNORECASE,
)
SENTENCE_BOUNDARY_RE = re.compile(r"[.;!?]\s")
#: A clause boundary: a colon, a comma, a semicolon, a bracket, a dash or a
#: coordinating conjunction. A negation on the other side of one applies to
#: that clause only, not to the phrase that follows it.
CLAUSE_BOUNDARY_RE = re.compile(
    r"[:,;()\[\]\u2013\u2014]|\s-\s"
    r"|\b(?:and|but|while|whereas|although|though|however)\b",
    re.IGNORECASE,
)
#: How far back a negation may sit, inside its own clause, and still apply
#: to the matched phrase.
NEGATION_WINDOW = 40

# --- Source normalisation ----------------------------------------------------

#: CSS values that legitimately contain a ``%`` (gradient stops, calc()) are
#: not copy.
CSS_VALUE_RE = re.compile(
    r"(?:radial|linear|conic)-gradient\((?:[^()]|\([^()]*\))*\)"
    r"|rgba?\((?:[^()]|\([^()]*\))*\)"
    r"|hsla?\((?:[^()]|\([^()]*\))*\)"
    r"|url\((?:[^()]|\([^()]*\))*\)"
    r"|calc\((?:[^()]|\([^()]*\))*\)",
    re.IGNORECASE,
)
CLASS_NAME_RE = re.compile(r"""className\s*=\s*(?:"[^"]*"|\{[^{}]*\})""", re.DOTALL)
STYLE_ATTR_RE = re.compile(
    r"""style\s*=\s*(?:"[^"]*"|\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\{[^{}]*\})""",
    re.DOTALL,
)
#: Animation configuration. These attributes carry framer-motion objects
#: (including pixel and percentage values); they are code, not copy.
ANIMATION_ATTR_RE = re.compile(
    r"""\b(?:variants|transition|animate|initial|exit|whileHover|whileTap"""
    r"""|whileFocus|whileInView|viewport|mediaViewport|layout)"""
    r"""\s*=\s*(?:"[^"]*"|\{\{(?:[^{}]|\{[^{}]*\})*\}\}|\{[^{}]*\})""",
    re.DOTALL,
)
#: User-visible attribute values: an image alternative text, a tooltip, an
#: accessible name or a form placeholder. They are copy.
USER_VISIBLE_ATTR_RE = re.compile(
    r"""\b(?:alt|title|aria-label|placeholder)\s*=\s*"""
    r"""(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|\{(?P<expression>[^{}]*)\})""",
    re.DOTALL,
)
IMPORT_RE = re.compile(r"^\s*(?:import\s+.*|export\s+.*\s+from\s+.*)$", re.MULTILINE)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _blank(match: re.Match[str]) -> str:
    """Replace a match with spaces of the same length.

    The length is preserved so that the offsets of the remaining text - and
    therefore the line numbers in the error messages - stay exact.
    """

    return " " * len(match.group(0))


def user_visible_copy(source: str) -> list[tuple[int, str]]:
    """The user-visible attribute values of a source, with their offsets.

    ``alt``, ``title``, ``aria-label`` and ``placeholder`` are rendered to
    the reader, so their values are copy and are checked as copy. They are
    returned separately because they are extracted from the source rather
    than scanned in place.
    """

    found: list[tuple[int, str]] = []
    for match in USER_VISIBLE_ATTR_RE.finditer(source):
        value = match.group("double")
        if value is None:
            value = match.group("single")
        if value is None:
            value = match.group("expression")
        if value is None:
            continue
        value = value.strip()
        if value:
            found.append((match.start(), value))
    return found


def visible_text(source: str) -> str:
    """Strip the parts of a source file that are not user-visible copy.

    Class names, inline styles, animation configuration, gradient constants
    and comments are code, not copy:
    ``radial-gradient(circle, rgba(70,71,211,0.15) 0%, transparent 70%)`` is
    a colour ramp and must not trip the percentage rule. The user-visible
    attribute values are removed here as well; they are checked separately by
    :func:`user_visible_copy`.
    """

    text = BLOCK_COMMENT_RE.sub(_blank, source)
    text = LINE_COMMENT_RE.sub(_blank, text)
    text = CSS_VALUE_RE.sub(_blank, text)
    text = CLASS_NAME_RE.sub(_blank, text)
    text = STYLE_ATTR_RE.sub(_blank, text)
    text = ANIMATION_ATTR_RE.sub(_blank, text)
    text = USER_VISIBLE_ATTR_RE.sub(_blank, text)
    text = IMPORT_RE.sub(_blank, text)
    return text


def collapse_whitespace(text: str) -> str:
    """Replace every whitespace run with spaces, preserving the length.

    JSX wraps a sentence across several source lines, so a naive line-based
    window would cut ``It is not`` away from ``clinically validated``. The
    length is preserved so match offsets (and therefore line numbers) stay
    valid.
    """

    return re.sub(r"[^\S\n]+|\n", lambda match: " " * len(match.group(0)), text)


def is_negated(text: str, start: int) -> bool:
    """True when the match at ``start`` is directly negated.

    The negation has to apply to the matched phrase: it has to sit in the
    same sentence, in the same clause, within a short window before the
    match. A negation that belongs to an earlier clause ("Not a mock: 99.8%
    accuracy on real patients") does not excuse the claim after it.
    """

    prefix = text[:start]
    sentence = SENTENCE_BOUNDARY_RE.split(prefix)[-1]
    clause = CLAUSE_BOUNDARY_RE.split(sentence)[-1]
    return bool(NEGATION_RE.search(clause[-NEGATION_WINDOW:]))


def landing_sources() -> list[Path]:
    sources: list[Path] = []
    for directory in (LANDING_DIR, LEGAL_DIR):
        if directory.is_dir():
            sources.extend(
                sorted(path for path in directory.rglob("*") if path.suffix in SOURCE_SUFFIXES)
            )
    sources.extend(path for path in PUBLIC_SOURCES if path.exists())
    return sources


def missing_public_sources() -> list[Path]:
    """The public sources that are not in the tree.

    A missing file would silently shrink the scanned set, so it is reported
    as an error rather than ignored.
    """

    return [path for path in PUBLIC_SOURCES if not path.exists()]


def _scan_text(
    text: str,
    relative: str,
    line_for: "callable[[int], int]",
) -> list[str]:
    """Return one error string per forbidden pattern found in ``text``."""

    errors: list[str] = []

    def report(pattern: re.Pattern[str], label: str, start: int, match: re.Match[str]) -> None:
        errors.append(
            f"{relative}:{line_for(start)}: forbidden claim {label!r} ({match.group(0)!r})"
        )

    for match in PERCENTAGE_RE.finditer(text):
        if is_negated(text, match.start()):
            continue
        report(
            PERCENTAGE_RE,
            "numeric claim: publish a percentage only when this repository holds "
            "the measurement",
            match.start(),
            match,
        )
    for match in MILLION_RE.finditer(text):
        if is_negated(text, match.start()):
            continue
        report(MILLION_RE, "scale claim", match.start(), match)
    for match in RECORDS_RE.finditer(text):
        if is_negated(text, match.start()):
            continue
        report(RECORDS_RE, "training-corpus claim", match.start(), match)
    for label, pattern in PHRASE_PATTERNS:
        for match in pattern.finditer(text):
            if is_negated(text, match.start()):
                continue
            report(pattern, label, match.start(), match)
    for label, pattern in ENDORSEMENT_PATTERNS:
        for match in pattern.finditer(text):
            if is_negated(text, match.start()):
                continue
            report(pattern, f"endorsement framing {label!r}", match.start(), match)
    for label, pattern in INSTITUTION_PATTERNS:
        for match in pattern.finditer(text):
            report(pattern, f"third-party institution {label!r}", match.start(), match)
    return errors


def check_source(path: Path) -> list[str]:
    """Return one error string per forbidden pattern found in ``path``."""

    raw = path.read_text(encoding="utf-8")
    visible = visible_text(raw)
    text = collapse_whitespace(visible)
    try:
        relative = path.relative_to(ROOT).as_posix()
    except ValueError:  # a snippet outside the repository (used by the tests)
        relative = path.name

    errors = _scan_text(text, relative, lambda start: visible.count("\n", 0, start) + 1)

    # The user-visible attribute values are copy in their own right.
    for offset, value in user_visible_copy(raw):
        line = raw.count("\n", 0, offset) + 1
        errors.extend(
            _scan_text(
                collapse_whitespace(value),
                relative,
                lambda _start, line=line: line,
            )
        )
    return errors


def run() -> tuple[int, list[str]]:
    sources = landing_sources()
    errors: list[str] = []
    for path in missing_public_sources():
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = path.name
        errors.append(f"{relative}: the public source is missing: the claims check would pass vacuously")
    if not sources:
        return 0, ["no landing sources found: the claims check would pass vacuously"]
    for path in sources:
        errors.extend(check_source(path))
    return len(sources), errors


def main() -> int:
    checks, errors = run()
    if errors:
        print(f"FAIL  landing claim check ({len(errors)} problem(s))")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"PASS  landing claim check ({checks} sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
