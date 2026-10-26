"""The condition-code catalogue (T108).

Condition codes are global reference data, not tenant data: every clinic uses
the same 14 codes so a tooth state means the same thing across the platform and
reporting never has to reconcile clinic-specific spellings.

The catalogue is a constant here and is written by the ``0002_seed_conditions``
data migration with ``update_or_create`` keyed on ``code``, which makes the
migration idempotent: re-running it (or re-running it after a label change)
converges instead of duplicating rows.

``default_status`` is the layer a fresh state starts in (``extraction_planned``
is planned; everything else is an existing finding). ``surfaces_required`` is
exposed to the UI so it can prompt for a surface pick; the API does not reject
an empty surface set (a radiograph-only finding such as a periapical lesion has
no surface).
"""

from __future__ import annotations

from typing import Any

# category values, in display order
CATEGORY_DIAGNOSIS = "diagnosis"
CATEGORY_RESTORATION = "restoration"
CATEGORY_ENDODONTIC = "endodontic"
CATEGORY_PROSTHETIC = "prosthetic"
CATEGORY_SURGICAL = "surgical"
CATEGORY_PREVENTIVE = "preventive"

CATEGORIES: tuple[tuple[str, str], ...] = (
    (CATEGORY_DIAGNOSIS, "Diagnosis"),
    (CATEGORY_RESTORATION, "Restoration"),
    (CATEGORY_ENDODONTIC, "Endodontic"),
    (CATEGORY_PROSTHETIC, "Prosthetic"),
    (CATEGORY_SURGICAL, "Surgical"),
    (CATEGORY_PREVENTIVE, "Preventive"),
)

CONDITION_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "code": "caries",
        "label": "Caries",
        "category": CATEGORY_DIAGNOSIS,
        "default_status": "existing",
        "surfaces_required": True,
        "sort_order": 10,
    },
    {
        "code": "periapical_lesion",
        "label": "Periapical lesion",
        "category": CATEGORY_DIAGNOSIS,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 20,
    },
    {
        "code": "fracture",
        "label": "Fracture",
        "category": CATEGORY_DIAGNOSIS,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 30,
    },
    {
        "code": "sensitivity",
        "label": "Sensitivity",
        "category": CATEGORY_DIAGNOSIS,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 40,
    },
    {
        "code": "missing",
        "label": "Missing",
        "category": CATEGORY_DIAGNOSIS,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 50,
    },
    {
        "code": "restoration_composite",
        "label": "Composite restoration",
        "category": CATEGORY_RESTORATION,
        "default_status": "existing",
        "surfaces_required": True,
        "sort_order": 110,
    },
    {
        "code": "restoration_amalgam",
        "label": "Amalgam restoration",
        "category": CATEGORY_RESTORATION,
        "default_status": "existing",
        "surfaces_required": True,
        "sort_order": 120,
    },
    {
        "code": "root_canal",
        "label": "Root canal treatment",
        "category": CATEGORY_ENDODONTIC,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 210,
    },
    {
        "code": "crown",
        "label": "Crown",
        "category": CATEGORY_PROSTHETIC,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 310,
    },
    {
        "code": "veneer",
        "label": "Veneer",
        "category": CATEGORY_PROSTHETIC,
        "default_status": "existing",
        "surfaces_required": True,
        "sort_order": 320,
    },
    {
        "code": "bridge",
        "label": "Bridge",
        "category": CATEGORY_PROSTHETIC,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 330,
    },
    {
        "code": "implant",
        "label": "Implant",
        "category": CATEGORY_PROSTHETIC,
        "default_status": "existing",
        "surfaces_required": False,
        "sort_order": 340,
    },
    {
        "code": "extraction_planned",
        "label": "Extraction planned",
        "category": CATEGORY_SURGICAL,
        "default_status": "planned",
        "surfaces_required": False,
        "sort_order": 410,
    },
    {
        "code": "sealant",
        "label": "Sealant",
        "category": CATEGORY_PREVENTIVE,
        "default_status": "existing",
        "surfaces_required": True,
        "sort_order": 510,
    },
)

CONDITION_CODES: tuple[str, ...] = tuple(seed["code"] for seed in CONDITION_SEEDS)
CONDITION_CODE_SET: frozenset[str] = frozenset(CONDITION_CODES)
DEFAULT_STATUS_BY_CODE: dict[str, str] = {
    seed["code"]: seed["default_status"] for seed in CONDITION_SEEDS
}


def apply_condition_seeds(model: Any) -> int:
    """Upsert every seed into ``model``; returns the number of rows written.

    ``model`` is passed in (rather than imported) so the same function serves
    the data migration's historical model and any future runtime re-seed.
    """

    written = 0
    for seed in CONDITION_SEEDS:
        defaults = {key: value for key, value in seed.items() if key != "code"}
        defaults.setdefault("is_active", True)
        model.objects.update_or_create(code=seed["code"], defaults=defaults)
        written += 1
    return written


def condition_payload(row: Any) -> dict[str, Any]:
    return {
        "code": row.code,
        "label": row.label,
        "category": row.category,
        "default_status": row.default_status,
        "surfaces_required": row.surfaces_required,
        "sort_order": row.sort_order,
    }


def legend(*, active_only: bool = True) -> list[dict[str, Any]]:
    """Return the catalogue as a list of plain dicts for the chart legend."""

    from .models import ConditionCode

    queryset = ConditionCode.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)
    queryset = queryset.order_by("sort_order", "code")
    return [condition_payload(row) for row in queryset]


__all__ = [
    "CATEGORIES",
    "CATEGORY_DIAGNOSIS",
    "CATEGORY_ENDODONTIC",
    "CATEGORY_PREVENTIVE",
    "CATEGORY_PROSTHETIC",
    "CATEGORY_RESTORATION",
    "CATEGORY_SURGICAL",
    "CONDITION_CODES",
    "CONDITION_CODE_SET",
    "CONDITION_SEEDS",
    "DEFAULT_STATUS_BY_CODE",
    "apply_condition_seeds",
    "condition_payload",
    "legend",
]
