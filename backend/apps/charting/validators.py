"""Surface, status and severity validation for the odontogram.

Surface validity depends on the tooth, not just on the value:

* anterior teeth (incisors, canines) have an incisal edge (``I``) and no
  occlusal surface, so ``{M, D, B, L, I}`` is allowed;
* posterior teeth (premolars, molars) have an occlusal surface and no incisal
  edge, so ``{M, O, D, B, L}`` is allowed.

Every rejection is field-addressed (``{"surfaces": ["..."]}``) so the batch
endpoint can report *which* change and *which* field failed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Canonical surface order. The stored list keeps the caller's order, but this
# is the order used for documentation and legend output.
SURFACES: tuple[str, ...] = ("M", "O", "D", "B", "L", "I")
SURFACE_SET = frozenset(SURFACES)

ANTERIOR_SURFACES: frozenset[str] = frozenset({"M", "D", "B", "L", "I"})
POSTERIOR_SURFACES: frozenset[str] = frozenset({"M", "O", "D", "B", "L"})

# Condition codes that remove the tooth from the surface-editable set.
MISSING_CONDITIONS: frozenset[str] = frozenset({"missing", "extraction_planned"})

STATUS_VALUES: tuple[str, ...] = ("existing", "planned", "referred", "completed")
STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    ("existing", "Existing"),
    ("planned", "Planned"),
    ("referred", "Referred"),
    ("completed", "Completed"),
)
# ``archived`` is not a stored status: archiving is ``archived_at``. It is a
# legal *transition target* so the UI can express "remove this state".
TRANSITION_TARGETS: tuple[str, ...] = (*STATUS_VALUES, "archived")

SEVERITY_MIN = 0
SEVERITY_MAX = 9


class ChartValidationError(ValueError):
    """A validation failure carrying field-addressed errors.

    Kept framework-free (no DRF import) so the pure validators can be unit
    tested without a request; the service layer turns it into an ``ApiError``.
    """

    def __init__(self, message: str, *, field_errors: dict[str, list[str]] | None = None):
        super().__init__(message)
        self.message = message
        self.field_errors: dict[str, list[str]] = field_errors or {}


def allowed_surfaces(info: dict[str, Any]) -> frozenset[str]:
    """The surface set a tooth of this type may carry."""

    return ANTERIOR_SURFACES if info.get("is_anterior") else POSTERIOR_SURFACES


def validate_surfaces(
    surfaces: Any,
    info: dict[str, Any],
    *,
    field: str = "surfaces",
) -> list[str]:
    """Validate and return ``surfaces`` unchanged (order preserved).

    Rejects a non-list, unknown values, duplicates and values that are invalid
    for the tooth type. Raises :class:`ChartValidationError` with a
    field-addressed error list.
    """

    if surfaces is None:
        return []
    if isinstance(surfaces, (str, bytes)) or not isinstance(surfaces, Iterable):
        raise ChartValidationError(
            "Surfaces must be a list.",
            field_errors={field: ["must be a list of surface codes"]},
        )

    allowed = allowed_surfaces(info)
    errors: list[str] = []
    seen: list[str] = []
    for value in surfaces:
        if not isinstance(value, str) or value not in SURFACE_SET:
            errors.append(
                f"unknown surface {value!r}; allowed values are {', '.join(SURFACES)}"
            )
            continue
        if value in seen:
            errors.append(f"duplicate surface {value}")
            continue
        if value not in allowed:
            errors.append(
                f"surface {value} is not valid for the "
                f"{'anterior' if info.get('is_anterior') else 'posterior'} tooth "
                f"{info.get('code')}"
            )
            continue
        seen.append(value)

    if errors:
        raise ChartValidationError("Invalid surface set.", field_errors={field: errors})
    return seen


def validate_status(status: Any, *, field: str = "status") -> str:
    if status is None:
        raise ChartValidationError(
            "A status is required.", field_errors={field: ["this field is required"]}
        )
    if status not in STATUS_VALUES:
        raise ChartValidationError(
            "Invalid status.",
            field_errors={field: [f"select one of: {', '.join(STATUS_VALUES)}"]},
        )
    return status


def validate_severity(severity: Any, *, field: str = "severity") -> int | None:
    if severity is None:
        return None
    if isinstance(severity, bool):
        raise ChartValidationError(
            "Invalid severity.",
            field_errors={field: [f"enter a value between {SEVERITY_MIN} and {SEVERITY_MAX}"]},
        )
    try:
        value = int(severity)
    except (TypeError, ValueError):
        raise ChartValidationError(
            "Invalid severity.",
            field_errors={field: [f"enter a value between {SEVERITY_MIN} and {SEVERITY_MAX}"]},
        ) from None
    if value < SEVERITY_MIN or value > SEVERITY_MAX:
        raise ChartValidationError(
            "Invalid severity.",
            field_errors={field: [f"enter a value between {SEVERITY_MIN} and {SEVERITY_MAX}"]},
        )
    return value


def is_missing_condition(condition_code: Any) -> bool:
    return str(condition_code) in MISSING_CONDITIONS


__all__ = [
    "ANTERIOR_SURFACES",
    "MISSING_CONDITIONS",
    "POSTERIOR_SURFACES",
    "SEVERITY_MAX",
    "SEVERITY_MIN",
    "STATUS_CHOICES",
    "STATUS_VALUES",
    "SURFACE_SET",
    "SURFACES",
    "TRANSITION_TARGETS",
    "ChartValidationError",
    "allowed_surfaces",
    "is_missing_condition",
    "validate_severity",
    "validate_status",
    "validate_surfaces",
]
