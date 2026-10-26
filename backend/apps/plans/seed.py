"""Default consent templates (T117).

Two clinic-neutral consent templates are seeded by migration
``0002_seed_consent_templates`` for every clinic that already exists and by
:func:`ensure_default_consent_templates` for new clinics. The list is
append-only: bump a template through the API (which creates version N+1)
instead of editing an entry here.
"""

from __future__ import annotations

from typing import Any

from .models import ConsentTemplate

DEFAULT_CONSENT_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "treatment_consent",
        "name": "Treatment consent",
        "purpose": "treatment",
        "locale": "en",
        "body": (
            "I consent to the dental treatment explained to me, including its "
            "risks, benefits and alternatives. I understand I may withdraw "
            "consent at any time."
        ),
        "approval_state": ConsentTemplate.ApprovalState.APPROVED,
        "is_active": True,
    },
    {
        "key": "anesthesia_consent",
        "name": "Local anaesthesia consent",
        "purpose": "anaesthesia",
        "locale": "en",
        "body": (
            "I consent to the administration of local anaesthesia for my "
            "dental procedure and understand the common and rare risks."
        ),
        "approval_state": ConsentTemplate.ApprovalState.APPROVED,
        "is_active": True,
    },
]


def ensure_default_consent_templates(clinic: Any) -> list[ConsentTemplate]:
    """Create any missing default consent templates for ``clinic``."""

    created: list[ConsentTemplate] = []
    for spec in DEFAULT_CONSENT_TEMPLATES:
        template = ConsentTemplate.all_objects.filter(
            clinic=clinic, key=spec["key"]
        ).first()
        if template is None:
            template = ConsentTemplate.all_objects.create(
                clinic=clinic,
                version=1,
                **spec,
            )
        created.append(template)
    return created


__all__ = ["DEFAULT_CONSENT_TEMPLATES", "ensure_default_consent_templates"]
