"""FDI notation: all 52 codes, quadrants, arches, laterality, dentition (T108).

The expectations below are written out literally rather than derived with the
same arithmetic as the implementation, so the test can actually fail.
"""

from __future__ import annotations

from datetime import date

import pytest

from apps.charting import fdi

PERMANENT_QUADRANTS = {
    1: ("upper", "right"),
    2: ("upper", "left"),
    3: ("lower", "left"),
    4: ("lower", "right"),
}
PRIMARY_QUADRANTS = {
    5: ("upper", "right"),
    6: ("upper", "left"),
    7: ("lower", "left"),
    8: ("lower", "right"),
}
PERMANENT_TYPES = {
    1: "incisor",
    2: "incisor",
    3: "canine",
    4: "premolar",
    5: "premolar",
    6: "molar",
    7: "molar",
    8: "molar",
}
PRIMARY_TYPES = {
    1: "incisor",
    2: "incisor",
    3: "canine",
    4: "molar",
    5: "molar",
}

PERMANENT_CODES = [q * 10 + p for q in (1, 2, 3, 4) for p in range(1, 9)]
PRIMARY_CODES = [q * 10 + p for q in (5, 6, 7, 8) for p in range(1, 6)]
ALL_CODES = sorted(PERMANENT_CODES + PRIMARY_CODES)


def test_table_has_exactly_52_codes():
    assert fdi.TOTAL_TOOTH_CODES == 52
    assert fdi.all_codes() == ALL_CODES
    assert len(fdi.all_codes()) == 52


def test_permanent_and_primary_code_sets():
    assert fdi.codes_for_dentition("permanent") == sorted(PERMANENT_CODES)
    assert fdi.codes_for_dentition("primary") == sorted(PRIMARY_CODES)
    assert fdi.codes_for_dentition("mixed") == ALL_CODES
    assert len(fdi.codes_for_dentition("permanent")) == 32
    assert len(fdi.codes_for_dentition("primary")) == 20


@pytest.mark.parametrize("code", ALL_CODES)
def test_every_code_round_trips(code):
    info = fdi.tooth_info(code)
    quadrant, position = divmod(code, 10)
    permanent = quadrant in PERMANENT_QUADRANTS
    arch, laterality = (PERMANENT_QUADRANTS if permanent else PRIMARY_QUADRANTS)[quadrant]
    tooth_type = (PERMANENT_TYPES if permanent else PRIMARY_TYPES)[position]

    assert info["code"] == code
    assert info["quadrant"] == quadrant
    assert info["arch"] == arch
    assert info["laterality"] == laterality
    assert info["type"] == tooth_type
    assert info["dentition"] == ("permanent" if permanent else "primary")
    assert info["is_anterior"] is (tooth_type in ("incisor", "canine"))
    assert isinstance(info["label"], str) and info["label"]
    assert arch in info["label"] and laterality in info["label"]


def test_lower_arch_laterality_follows_iso_3950():
    """31-38 is the patient's left; 41-48 the patient's right."""

    assert {code: fdi.tooth_info(code)["laterality"] for code in range(31, 39)} == {
        code: "left" for code in range(31, 39)
    }
    assert {code: fdi.tooth_info(code)["laterality"] for code in range(41, 49)} == {
        code: "right" for code in range(41, 49)
    }
    # Upper arch mirrors the standard too.
    assert all(fdi.tooth_info(code)["laterality"] == "right" for code in range(11, 19))
    assert all(fdi.tooth_info(code)["laterality"] == "left" for code in range(21, 29))
    # Primary quadrants use the same laterality convention.
    assert all(fdi.tooth_info(code)["laterality"] == "right" for code in range(51, 56))
    assert all(fdi.tooth_info(code)["laterality"] == "left" for code in range(61, 66))
    assert all(fdi.tooth_info(code)["laterality"] == "left" for code in range(71, 76))
    assert all(fdi.tooth_info(code)["laterality"] == "right" for code in range(81, 86))


def test_arch_assignment():
    for code in list(range(11, 19)) + list(range(21, 29)) + list(range(51, 56)) + list(range(61, 66)):
        assert fdi.tooth_info(code)["arch"] == "upper"
    for code in list(range(31, 39)) + list(range(41, 49)) + list(range(71, 76)) + list(range(81, 86)):
        assert fdi.tooth_info(code)["arch"] == "lower"


def test_primary_molars_are_not_premolars():
    assert fdi.tooth_info(54)["type"] == "molar"
    assert fdi.tooth_info(55)["type"] == "molar"
    assert fdi.tooth_info(14)["type"] == "premolar"
    assert fdi.tooth_info(15)["type"] == "premolar"
    assert fdi.tooth_info(16)["type"] == "molar"


def test_labels_are_unique():
    labels = [fdi.tooth_info(code)["label"] for code in ALL_CODES]
    assert len(set(labels)) == 52


def test_tooth_info_rejects_unknown_codes():
    for bad in (19, 29, 39, 49, 56, 66, 76, 86, 10, 90, 0, "x", None):
        with pytest.raises(ValueError):
            fdi.tooth_info(bad)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "primary"),
        (2, "primary"),
        (4, "primary"),
        (5, "mixed"),
        (6, "mixed"),
        (9, "mixed"),
        (12, "mixed"),
        (13, "mixed"),
        (14, "permanent"),
        (30, "permanent"),
    ],
)
def test_derived_dentition_by_age(age, expected):
    as_of = date(2026, 6, 15)
    born = date(2026 - age, 6, 15)
    assert fdi.derived_dentition(born, as_of) == expected


def test_derived_dentition_boundary_before_birthday():
    as_of = date(2026, 6, 14)
    # Turns 5 the next day: still 4, so still primary.
    assert fdi.derived_dentition(date(2021, 6, 15), as_of) == "primary"
    assert fdi.derived_dentition(date(2021, 6, 14), as_of) == "mixed"


def test_derived_dentition_defaults_to_permanent_without_a_dob():
    assert fdi.derived_dentition(None) == "permanent"


def test_validate_tooth_matches_dentition():
    assert fdi.validate_tooth(11, "permanent")["code"] == 11
    assert fdi.validate_tooth(51, "primary")["code"] == 51
    assert fdi.validate_tooth(11, "mixed")["code"] == 11
    assert fdi.validate_tooth(51, "mixed")["code"] == 51

    with pytest.raises(ValueError):
        fdi.validate_tooth(11, "primary")
    with pytest.raises(ValueError):
        fdi.validate_tooth(51, "permanent")
    with pytest.raises(ValueError):
        fdi.validate_tooth(99, "mixed")
    with pytest.raises(ValueError):
        fdi.validate_tooth(11, "banana")
