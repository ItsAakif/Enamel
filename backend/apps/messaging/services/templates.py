"""Template rendering and versioning (T114).

Rendering enforces a per-template variable allowlist: a placeholder that is not
declared in ``template.variables`` raises :class:`TemplateError`, and so does a
declared placeholder with no value in the context. A template edit bumps
``version`` and writes an audit row; because every ``Message`` keeps the integer
``template_version`` it rendered with, an edit never rewrites the history of
messages already sent.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from apps.core.audit import record as record_audit
from apps.core.tenancy import with_clinic

from ..models import MessageTemplate

#: Fields an edit through the API may change.
EDITABLE_FIELDS = (
    "name",
    "channel",
    "purpose",
    "locale",
    "subject",
    "body",
    "variables",
    "approval_status",
    "is_active",
)


class TemplateError(RuntimeError):
    """A template could not be rendered (unknown or missing variable)."""


@dataclass(frozen=True)
class Rendered:
    subject: str
    body: str


def _used_fields(text: str) -> set[str]:
    if not text:
        return set()
    fields: set[str] = set()
    for _literal, field_name, _spec, _conversion in string.Formatter().parse(text):
        if field_name is None:
            continue
        root = field_name.split(".")[0].split("[")[0].strip()
        if root:
            fields.add(root)
    return fields


def get_template(clinic: Any, key: str) -> MessageTemplate | None:
    """Return the clinic's active template for ``key`` (any approval state)."""

    return MessageTemplate.all_objects.filter(clinic=clinic, key=key).first()


def render(template: MessageTemplate, context: dict[str, Any] | None = None) -> Rendered:
    """Render ``template`` against ``context``; raise ``TemplateError`` on abuse."""

    values = context or {}
    allowed = {str(item) for item in (template.variables or [])}
    body = template.body or ""
    subject = template.subject or ""

    used = _used_fields(body) | _used_fields(subject)
    unknown = sorted(used - allowed)
    if unknown:
        raise TemplateError("Unknown template variable(s): " + ", ".join(unknown))
    missing = sorted(name for name in used if name not in values)
    if missing:
        raise TemplateError("Missing value for template variable(s): " + ", ".join(missing))

    try:
        rendered_body = body.format(**values) if body else ""
        rendered_subject = subject.format(**values) if subject else ""
    except (KeyError, IndexError, ValueError) as exc:
        raise TemplateError(f"Template render failed: {exc}") from exc
    return Rendered(subject=rendered_subject, body=rendered_body)


def snapshot(template: MessageTemplate) -> dict[str, Any]:
    return {
        "key": template.key,
        "version": template.version,
        "channel": template.channel,
        "purpose": template.purpose,
        "approval_status": template.approval_status,
        "is_active": template.is_active,
    }


def edit(
    template: MessageTemplate,
    *,
    fields: dict[str, Any],
    actor: Any = None,
    request: Any = None,
) -> MessageTemplate:
    """Apply ``fields`` to ``template`` and create version ``N+1``."""

    changes = {name: value for name, value in fields.items() if name in EDITABLE_FIELDS}
    if not changes:
        return template

    clinic = template.clinic
    with transaction.atomic():
        with with_clinic(clinic):
            before = snapshot(template)
            for name, value in changes.items():
                setattr(template, name, value)
            template.version = template.version + 1
            template.save(update_fields=[*changes, "version", "updated_at"])
            record_audit(
                "messaging.template_updated",
                entity=template,
                before=before,
                after=snapshot(template),
                actor=actor,
                request=request,
            )
    return template


__all__ = [
    "EDITABLE_FIELDS",
    "Rendered",
    "TemplateError",
    "edit",
    "get_template",
    "render",
    "snapshot",
]
