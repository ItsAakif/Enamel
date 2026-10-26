"""Segment predicate DSL and evaluation (T132).

A predicate is a small JSON document::

    {"all": [{"field": "last_visit_days", "op": "gte", "value": 180}]}

* ``all`` requires every condition to match; ``any`` requires at least one.
* ``field`` is validated against the non-clinical allowlist in
  :mod:`apps.marketing.services.guardrails`.
* ``op`` is one of ``eq``, ``gte``, ``lte``, ``in``, ``between``.

Evaluation is intentionally simple and deterministic: every patient in the
clinic is projected onto the allowlisted fields once, then the predicate is
applied in Python. Clinical notes, diagnoses, allergies and odontogram rows are
never loaded, so a segment can only ever select non-clinical facts.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.patients.models import Encounter, Patient

from . import guardrails
from .guardrails import ALLOWED_FIELDS, ALLOWED_OPS, LOGICAL_KEYS, PredicateError

_NUMERIC_OPS = ("gte", "lte")


def _coerce_number(value: Any, *, field: str, op: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PredicateError(f"operator '{op}' on '{field}' requires a numeric value")
    return float(value)


def _validate_condition(condition: Any) -> dict[str, Any]:
    if not isinstance(condition, dict):
        raise PredicateError("each condition must be an object with field, op and value")
    if "field" not in condition or "op" not in condition:
        raise PredicateError("each condition requires a field and an op")
    if "value" not in condition:
        raise PredicateError("each condition requires a value")

    field = guardrails.check_field(condition.get("field"))
    op = str(condition.get("op") or "").strip().lower()
    if op not in ALLOWED_OPS:
        raise PredicateError(f"unsupported operator '{op}'")
    value = condition.get("value")

    if op in _NUMERIC_OPS:
        _coerce_number(value, field=field, op=op)
    elif op == "between":
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)
        ):
            raise PredicateError(f"'between' on '{field}' requires [low, high]")
    elif op == "in":
        if not isinstance(value, (list, tuple)) or not value:
            raise PredicateError(f"'in' on '{field}' requires a non-empty list")
    return {"field": field, "op": op, "value": value}


def validate_predicate(predicate: Any) -> dict[str, Any]:
    """Validate and normalise ``predicate`` or raise :class:`PredicateError`."""

    if not isinstance(predicate, dict) or not predicate:
        raise PredicateError("a segment predicate must be a non-empty object")

    present = [key for key in LOGICAL_KEYS if key in predicate]
    if len(present) != 1:
        raise PredicateError("a segment predicate must contain exactly one of 'all' or 'any'")

    key = present[0]
    conditions = predicate.get(key)
    if not isinstance(conditions, (list, tuple)) or not conditions:
        raise PredicateError(f"'{key}' must be a non-empty list of conditions")
    return {key: [_validate_condition(item) for item in conditions]}


def _match_condition(fields: dict[str, Any], condition: dict[str, Any]) -> bool:
    actual = fields.get(condition["field"])
    op = condition["op"]
    value = condition["value"]

    if op == "eq":
        return actual == value
    if actual is None:
        return False
    if op == "gte":
        return actual >= value
    if op == "lte":
        return actual <= value
    if op == "in":
        return actual in value
    if op == "between":
        low, high = value
        return low <= actual <= high
    return False


def evaluate(fields: dict[str, Any], predicate: dict[str, Any]) -> bool:
    """Apply a validated predicate to one projected patient field mapping."""

    for key in LOGICAL_KEYS:
        if key in predicate:
            results = (_match_condition(fields, c) for c in predicate[key])
            return all(results) if key == "all" else any(results)
    return False


def _plan_status_map(clinic: Any) -> dict[Any, str]:
    """Latest treatment-plan status per patient (read-only, id link only)."""

    TreatmentPlan = apps.get_model("plans", "TreatmentPlan")
    rows = (
        TreatmentPlan.all_objects.filter(clinic=clinic)
        .order_by("patient_id", "-created_at", "-id")
        .values_list("patient_id", "status")
    )
    statuses: dict[Any, str] = {}
    for patient_id, status in rows:
        statuses.setdefault(patient_id, status)
    return statuses


def resolve_rows(clinic: Any, *, now: Any = None) -> list[dict[str, Any]]:
    """Project every active patient in ``clinic`` onto the allowlisted fields."""

    from apps.messaging.models import RecallStatus

    moment = now or timezone.now()
    last_visit = (
        Encounter.all_objects.filter(
            clinic=clinic,
            status=Encounter.Status.FINALIZED,
            patient_id=OuterRef("pk"),
        )
        .order_by("-occurred_at")
        .values("occurred_at")[:1]
    )
    patients = (
        Patient.all_objects.filter(clinic=clinic, deleted_at__isnull=True)
        .annotate(last_visit_at=Subquery(last_visit))
        .order_by("last_name", "first_name", "id")
    )
    recall_due = set(
        RecallStatus.all_objects.filter(
            clinic=clinic,
            status__in=[RecallStatus.Status.DUE, RecallStatus.Status.SCHEDULED],
            due_at__lte=moment,
        ).values_list("patient_id", flat=True)
    )
    plan_status = _plan_status_map(clinic)

    rows: list[dict[str, Any]] = []
    for patient in patients:
        last_visit_at = patient.last_visit_at
        fields = {
            "status": patient.status,
            "last_visit_days": (
                (moment - last_visit_at).days if last_visit_at is not None else None
            ),
            "recall_due": patient.pk in recall_due,
            "plan_status": plan_status.get(patient.pk),
            "location": str(patient.primary_location_id) if patient.primary_location_id else None,
            "source": patient.source,
        }
        rows.append(
            {
                "patient_id": str(patient.pk),
                "name": patient.full_name,
                "fields": fields,
            }
        )
    return rows


def preview(
    clinic: Any,
    predicate: Any,
    *,
    sample_size: int = 5,
    now: Any = None,
) -> dict[str, Any]:
    """Return the matched count plus a small, non-clinical sample."""

    normalized = validate_predicate(predicate)
    rows = resolve_rows(clinic, now=now)
    matched = [row for row in rows if evaluate(row["fields"], normalized)]
    sample = [
        {
            "patient_id": row["patient_id"],
            "name": row["name"],
            "fields": row["fields"],
        }
        for row in matched[: max(0, int(sample_size))]
    ]
    return {
        "count": len(matched),
        "sample": sample,
        "fields": list(ALLOWED_FIELDS),
    }


__all__ = [
    "ALLOWED_FIELDS",
    "ALLOWED_OPS",
    "evaluate",
    "preview",
    "resolve_rows",
    "validate_predicate",
]
