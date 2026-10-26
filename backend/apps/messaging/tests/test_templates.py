"""Template allowlist rendering and versioning (T114 acceptance 8, 9)."""

from __future__ import annotations

import pytest

from apps.messaging.models import Message, MessageTemplate
from apps.messaging.services import dispatch
from apps.messaging.services import templates as template_service
from apps.messaging.services.templates import TemplateError

from .conftest import DAYTIME

pytestmark = pytest.mark.django_db


def test_seed_templates_are_approved(templates, owner):
    created = templates(owner.clinic)

    assert {template.key for template in created} == {
        "appointment_reminder",
        "recall_reminder",
        "cancellation_confirmation",
    }
    assert all(template.approval_status == "approved" for template in created)
    assert all(template.version == 1 for template in created)


def test_render_uses_the_variable_allowlist(owner, templates):
    templates(owner.clinic)
    template = MessageTemplate.all_objects.get(clinic=owner.clinic, key="appointment_reminder")

    rendered = template_service.render(
        template,
        {"first_name": "Ada", "date": "Mon 05 Jan", "time": "10:00", "provider": "Dr X"},
    )

    assert "Ada" in rendered.body
    assert "{first_name}" not in rendered.body


def test_unknown_variable_raises(owner):
    template = MessageTemplate.all_objects.create(
        clinic=owner.clinic,
        key="bad",
        name="Bad",
        channel="email",
        purpose="transactional",
        body="Hi {first_name}, code {secret_code}",
        variables=["first_name"],
    )

    with pytest.raises(TemplateError):
        template_service.render(template, {"first_name": "Ada", "secret_code": "1234"})


def test_missing_value_raises(owner):
    template = MessageTemplate.all_objects.create(
        clinic=owner.clinic,
        key="needs-name",
        name="Needs name",
        channel="email",
        purpose="transactional",
        body="Hi {first_name}",
        variables=["first_name"],
    )

    with pytest.raises(TemplateError):
        template_service.render(template, {})


def test_edit_bumps_version_and_messages_keep_their_version(
    owner, patient_factory, templates, grant_consent
):
    templates(owner.clinic)
    patient = patient_factory(owner.clinic)
    grant_consent(patient)

    message = dispatch.send(
        patient,
        "email",
        "appointment_reminders",
        "appointment_reminder",
        {"first_name": "Ada", "date": "Mon", "time": "10:00", "provider": "Dr"},
        idempotency_key="version-1",
        now=DAYTIME,
    )
    assert message.template_version == 1

    template = MessageTemplate.all_objects.get(
        clinic=owner.clinic, key="appointment_reminder"
    )
    template_service.edit(
        template,
        fields={
            "body": "Hi {first_name}, reminder: {date} at {time} with {provider}.",
        },
    )
    assert template.version == 2

    message.refresh_from_db()
    assert message.template_version == 1
    assert Message.all_objects.filter(clinic=owner.clinic).count() == 1
