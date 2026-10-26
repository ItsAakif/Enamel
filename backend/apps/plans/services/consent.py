"""Consent template versioning (T117).

A consent template is versioned *in place*: an edit bumps ``version`` and the
new wording applies to future signatures only. Because every
:class:`~apps.plans.models.ConsentRecord` keeps the integer ``template_version``
it was signed against, editing a template never rewrites what a patient agreed
to, and an old consent still renders its own version.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import ConsentTemplate

#: Fields an edit through the API may change.
EDITABLE_FIELDS = (
    "name",
    "purpose",
    "body",
    "locale",
    "is_active",
    "approval_state",
)

SNAPSHOT_FIELDS = ("key", "name", "purpose", "locale", "version", "is_active", "approval_state")


def snapshot(template: ConsentTemplate) -> dict[str, Any]:
    """Audit projection for a consent template (no wording body)."""

    return {name: getattr(template, name) for name in SNAPSHOT_FIELDS}


def edit_template(
    template: ConsentTemplate,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> ConsentTemplate:
    """Apply ``fields`` and create version ``N+1``."""

    changes = {name: value for name, value in fields.items() if name in EDITABLE_FIELDS}
    if not changes:
        return template

    with transaction.atomic():
        with with_clinic(template.clinic):
            before = snapshot(template)
            for name, value in changes.items():
                setattr(template, name, value)
            template.version = template.version + 1
            template.save(update_fields=[*changes, "version", "updated_at"])
            record_audit(
                "consent.template_updated",
                entity=template,
                before=before,
                after=snapshot(template),
                actor=actor,
                request=request,
            )
    return template


__all__ = ["EDITABLE_FIELDS", "SNAPSHOT_FIELDS", "edit_template", "snapshot"]
