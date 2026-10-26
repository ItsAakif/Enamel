"""Redaction of audit payloads.

Audit rows may reference a patient by UUID, an enum value, an amount or a date,
but must never persist free-text clinical content or direct identifiers. This
module walks a ``before``/``after`` structure and replaces the value of any
sensitive key with ``"[redacted]"``.

Rules (T103):

* Keys in :data:`SENSITIVE_KEYS` are redacted at any nesting depth, including
  when the name is qualified (``address_json``, ``patient_full_name``).
* Any key whose lower-cased name ends in ``_notes`` is redacted, so new
  clinical note fields are covered without editing this list.
* Everything else is preserved, with UUIDs, datetimes and decimals normalised
  to strings so the value round-trips through ``JSONField`` unchanged.

Only key names decide redaction: a value is never inspected for "looks like
PII", because that heuristic both misses data and corrupts legitimate values.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

REDACTED = "[redacted]"

# Key names that must never carry a value into the audit log. Lower-case
# comparison is used so ``Notes`` and ``notes`` are treated identically.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "notes",
        "clinical_notes",
        "medical_history",
        "allergies",
        "message_body",
        "content",
        "description",
        "diagnosis",
        "treatment_notes",
        "address",
        "phone",
        "email",
        "date_of_birth",
        "full_name",
    }
)

# Suffixes that mark a key as free text. ``treatment_notes`` is already listed
# above; the suffix rule catches ``provider_notes``, ``visit_notes`` and any
# future variant.
SENSITIVE_SUFFIXES: tuple[str, ...] = ("_notes",)

# A sensitive name may be prefixed or suffixed with a qualifier without
# changing its meaning: ``address_json`` and ``primary_address`` are as
# sensitive as ``address``, ``patient_full_name`` as ``full_name``. Matching is
# therefore on underscore/dash-delimited *segments*, not just the whole key.
_SENSITIVE_SEGMENT_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:" + "|".join(re.escape(key) for key in sorted(SENSITIVE_KEYS)) + r")(?:$|[^a-z0-9])"
)


def is_sensitive_key(key: Any) -> bool:
    """Return True when ``key`` names a field that must be redacted."""

    lowered = str(key).lower()
    if lowered in SENSITIVE_KEYS:
        return True
    if any(lowered.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True
    return _SENSITIVE_SEGMENT_RE.search(lowered) is not None


def _normalise_scalar(value: Any) -> Any:
    """Convert a non-container value into something JSON-safe.

    Primitives pass through untouched; common identifier/temporal types become
    strings. An unknown object type becomes a type marker rather than ``repr``
    so an unexpected object can never leak its contents into the audit log.
    """

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return f"<{type(value).__name__}>"


def redact(value: Any) -> Any:
    """Return a redacted, JSON-safe copy of ``value`` (dicts/lists recursed)."""

    if isinstance(value, dict):
        return {
            str(key): (REDACTED if is_sensitive_key(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item) for item in value]
    return _normalise_scalar(value)


def changed_field_names(before: Any, after: Any) -> list[str]:
    """Return the sorted field names whose value differs between two snapshots.

    Only names are compared, and the list is pure metadata (no values), so it
    is safe to store directly in an audit row.
    """

    if not isinstance(before, dict) or not isinstance(after, dict):
        return sorted({str(key) for key in (after or {})}) if isinstance(after, dict) else []
    return sorted(
        str(key) for key in set(before) | set(after) if before.get(key) != after.get(key)
    )


__all__ = [
    "REDACTED",
    "SENSITIVE_KEYS",
    "SENSITIVE_SUFFIXES",
    "changed_field_names",
    "is_sensitive_key",
    "redact",
]
