"""Default message templates (T114).

The three approved templates below are seeded by migration ``0002_seed_templates``
for every clinic that already exists and by :func:`ensure_default_templates` for
new clinics (tests, demo seeds). The list is append-only: never edit an existing
entry in place, because the migration that seeded it must stay reproducible.
Bump a template through the API (which creates version N+1) instead.
"""

from __future__ import annotations

from typing import Any

from .models import Consent, MessageTemplate

WORDING_VERSION = "2026-01"

DEFAULT_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "appointment_reminder",
        "name": "Appointment reminder",
        "channel": Consent.Channel.EMAIL,
        "purpose": Consent.Purpose.APPOINTMENT_REMINDERS,
        "locale": "en",
        "subject": "Appointment reminder",
        "body": (
            "Hi {first_name}, this is a reminder of your appointment on {date} "
            "at {time} with {provider}. Reply STOP to opt out."
        ),
        "variables": ["first_name", "date", "time", "provider"],
        "approval_status": MessageTemplate.ApprovalStatus.APPROVED,
        "is_active": True,
    },
    {
        "key": "recall_reminder",
        "name": "Recall reminder",
        "channel": Consent.Channel.SMS,
        "purpose": Consent.Purpose.RECALL,
        "locale": "en",
        "subject": "",
        "body": (
            "Hi {first_name}, it is time for your {recall_name} check-up at "
            "{clinic}. Call us to book."
        ),
        "variables": ["first_name", "recall_name", "clinic"],
        "approval_status": MessageTemplate.ApprovalStatus.APPROVED,
        "is_active": True,
    },
    {
        "key": "cancellation_confirmation",
        "name": "Cancellation confirmation",
        "channel": Consent.Channel.EMAIL,
        "purpose": Consent.Purpose.TRANSACTIONAL,
        "locale": "en",
        "subject": "Appointment cancelled",
        "body": (
            "Hi {first_name}, your appointment on {date} at {time} has been "
            "cancelled. Reply STOP to opt out."
        ),
        "variables": ["first_name", "date", "time"],
        "approval_status": MessageTemplate.ApprovalStatus.APPROVED,
        "is_active": True,
    },
]


def ensure_default_templates(clinic: Any) -> list[MessageTemplate]:
    """Create any missing default templates for ``clinic`` and return them.

    Idempotent: an existing ``key`` is left untouched so a clinic's edited
    template is never overwritten by a re-run.
    """

    created: list[MessageTemplate] = []
    for spec in DEFAULT_TEMPLATES:
        template = MessageTemplate.all_objects.filter(
            clinic=clinic, key=spec["key"]
        ).first()
        if template is None:
            template = MessageTemplate.all_objects.create(
                clinic=clinic,
                version=1,
                **spec,
            )
        created.append(template)
    return created


__all__ = ["DEFAULT_TEMPLATES", "WORDING_VERSION", "ensure_default_templates"]
