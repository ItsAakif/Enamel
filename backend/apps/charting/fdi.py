"""FDI two-digit tooth notation (ISO 3950).

The whole notation is one table. That is deliberate: a "wrong tooth" defect is
a *data* defect, so it is caught by a single exhaustive test over every code
instead of by reviewing arithmetic scattered through views and serializers.

The first digit is the quadrant, counted clockwise from the patient's upper
right::

    1  upper right      2  upper left      (permanent)
    4  lower right      3  lower left
    5  upper right      6  upper left      (primary)
    8  lower right      7  lower left

The lower arch is the trap: ``31``-``38`` is the patient's **left** and
``41``-``48`` the patient's **right** (ISO 3950). This is the opposite of the
reading order of the digits, which is exactly why laterality lives in the table
and never in an ``if code < 40`` branch.

The second digit is the tooth position within the quadrant (1-8 permanent,
1-5 primary). Type rules: positions 1-2 are incisors, 3 is the canine, 4-5 are
premolars for permanent teeth but the first two molars for primary teeth, and
6-8 are molars.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Per-tooth dentition. A stored ``ToothState`` is always one of these.
DENTITION_VALUES: tuple[str, ...] = ("permanent", "primary")
DENTITION_CHOICES: tuple[tuple[str, str], ...] = (
    ("permanent", "Permanent"),
    ("primary", "Primary"),
)

# Patient-level dentition context: a 6-year-old is in *mixed* dentition, which
# is neither "all primary" nor "all permanent".
CHART_DENTITIONS: tuple[str, ...] = ("primary", "mixed", "permanent")
CHART_DENTITION_CHOICES: tuple[tuple[str, str], ...] = (
    ("primary", "Primary"),
    ("mixed", "Mixed"),
    ("permanent", "Permanent"),
)

# quadrant -> (arch, laterality, dentition)
_QUADRANTS: dict[int, tuple[str, str, str]] = {
    1: ("upper", "right", "permanent"),
    2: ("upper", "left", "permanent"),
    3: ("lower", "left", "permanent"),
    4: ("lower", "right", "permanent"),
    5: ("upper", "right", "primary"),
    6: ("upper", "left", "primary"),
    7: ("lower", "left", "primary"),
    8: ("lower", "right", "primary"),
}

_POSITION_NAMES: dict[str, dict[int, str]] = {
    "incisor": {1: "central incisor", 2: "lateral incisor"},
    "canine": {3: "canine"},
    "premolar": {4: "first premolar", 5: "second premolar"},
    "molar": {6: "first molar", 7: "second molar", 8: "third molar"},
}
_PRIMARY_MOLAR_NAMES: dict[int, str] = {4: "first molar", 5: "second molar"}

# Mixed dentition window (inclusive) as agreed in the odontogram spec.
MIXED_DENTITION_MIN_AGE = 5
MIXED_DENTITION_MAX_AGE = 13


def _type_for(dentition: str, position: int) -> str:
    if position <= 2:
        return "incisor"
    if position == 3:
        return "canine"
    if dentition == "primary":
        # Primary positions 4-5 are molars, not premolars.
        return "molar"
    return "premolar" if position in (4, 5) else "molar"


def _label(dentition: str, arch: str, laterality: str, tooth_type: str, position: int) -> str:
    if tooth_type == "molar" and dentition == "primary":
        name = _PRIMARY_MOLAR_NAMES[position]
    else:
        name = _POSITION_NAMES[tooth_type][position]
    prefix = "primary " if dentition == "primary" else ""
    return f"{arch} {laterality} {prefix}{name}"


def _build_table() -> dict[int, dict[str, Any]]:
    table: dict[int, dict[str, Any]] = {}
    for quadrant, (arch, laterality, dentition) in _QUADRANTS.items():
        last_position = 5 if dentition == "primary" else 8
        for position in range(1, last_position + 1):
            code = quadrant * 10 + position
            tooth_type = _type_for(dentition, position)
            table[code] = {
                "code": code,
                "quadrant": quadrant,
                "arch": arch,
                "laterality": laterality,
                "type": tooth_type,
                "dentition": dentition,
                "is_anterior": tooth_type in ("incisor", "canine"),
                "label": _label(dentition, arch, laterality, tooth_type, position),
            }
    return table


TOOTH_TABLE: dict[int, dict[str, Any]] = _build_table()
TOTAL_TOOTH_CODES = len(TOOTH_TABLE)  # 52


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _age_in_years(born: date, reference: date) -> int:
    years = reference.year - born.year
    if (reference.month, reference.day) < (born.month, born.day):
        years -= 1
    return years


def tooth_info(code: Any) -> dict[str, Any]:
    """Return the table row for ``code``; raises ``ValueError`` if unknown."""

    try:
        key = int(code)
    except (TypeError, ValueError):
        raise ValueError(f"unknown FDI tooth code: {code!r}") from None
    info = TOOTH_TABLE.get(key)
    if info is None:
        raise ValueError(f"unknown FDI tooth code: {code!r}")
    # A copy: callers may store it, and the table must stay immutable.
    return dict(info)


def all_codes() -> list[int]:
    """Every FDI code, ascending (11..48 permanent, 51..85 primary)."""

    return sorted(TOOTH_TABLE)


def codes_for_dentition(dentition: str) -> list[int]:
    """Codes belonging to ``dentition``; ``mixed`` means the union."""

    if dentition not in CHART_DENTITIONS:
        raise ValueError(f"unknown dentition: {dentition!r}")
    if dentition == "mixed":
        return all_codes()
    return sorted(code for code, info in TOOTH_TABLE.items() if info["dentition"] == dentition)


def derived_dentition(date_of_birth: Any, as_of: Any = None) -> str:
    """Derive the patient-level dentition from age.

    ``< 5`` primary, ``5..13`` mixed, ``>= 14`` permanent. A missing date of
    birth defaults to permanent, which is the safe assumption for an adult
    chart (and is surfaced as ``dentition_source="derived"`` so the UI can
    prompt for an override).
    """

    if date_of_birth is None:
        return "permanent"
    reference = _as_date(as_of) if as_of is not None else date.today()
    age = _age_in_years(_as_date(date_of_birth), reference)
    if age < MIXED_DENTITION_MIN_AGE:
        return "primary"
    if age <= MIXED_DENTITION_MAX_AGE:
        return "mixed"
    return "permanent"


def validate_tooth(code: Any, dentition: str = "mixed") -> dict[str, Any]:
    """Return the table row for ``code`` when it belongs to ``dentition``.

    ``mixed`` accepts both permanent and primary codes. Raises ``ValueError``
    for an unknown code or a dentition mismatch so callers can turn it into a
    field-addressed 400.
    """

    if dentition not in CHART_DENTITIONS:
        raise ValueError(f"unknown dentition: {dentition!r}")
    info = tooth_info(code)
    if dentition != "mixed" and info["dentition"] != dentition:
        raise ValueError(
            f"tooth {info['code']} does not belong to the {dentition} dentition"
        )
    return info


__all__ = [
    "CHART_DENTITION_CHOICES",
    "CHART_DENTITIONS",
    "DENTITION_CHOICES",
    "DENTITION_VALUES",
    "MIXED_DENTITION_MAX_AGE",
    "MIXED_DENTITION_MIN_AGE",
    "TOOTH_TABLE",
    "TOTAL_TOOTH_CODES",
    "all_codes",
    "codes_for_dentition",
    "derived_dentition",
    "tooth_info",
    "validate_tooth",
]
