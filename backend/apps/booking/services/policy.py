"""Per-clinic booking policy access (T112).

``get_policy`` is the single read/write entry point for ``BookingPolicy``. It is
get-or-create with the documented defaults, so the public endpoints never have
to special-case a clinic that has not been configured yet, and a test can build
a clinic and immediately book.
"""

from __future__ import annotations

from typing import Any

from apps.core.tenancy import with_clinic

from ..models import BookingPolicy

#: Fields surfaced in the public clinic payload. Ids/enums/ints only.
PUBLIC_POLICY_FIELDS = (
    "lead_time_hours",
    "window_days",
    "hold_minutes",
    "cancel_cutoff_hours",
    "reschedule_cutoff_hours",
    "require_email_verification",
    "allow_online_reschedule",
    "max_future_bookings",
    "consent_wording_version",
    "is_public_enabled",
)


def get_policy(clinic: Any) -> BookingPolicy:
    """Return the clinic's policy, creating it with defaults when missing."""

    with with_clinic(clinic):
        policy, _created = BookingPolicy.objects.get_or_create(clinic=clinic)
    return policy


def policy_summary(policy: BookingPolicy) -> dict[str, Any]:
    """Serialize the policy for the unauthenticated clinic payload."""

    return {field: getattr(policy, field) for field in PUBLIC_POLICY_FIELDS}


__all__ = ["PUBLIC_POLICY_FIELDS", "get_policy", "policy_summary"]
