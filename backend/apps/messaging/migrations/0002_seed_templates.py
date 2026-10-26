"""Seed the three approved default message templates (T114).

The template list is append-only: never edit an existing entry in place, because
this migration must stay reproducible. Bump a template through the API instead
(which creates version N+1). New clinics get the same templates through
``apps.messaging.seed.ensure_default_templates``.
"""

from __future__ import annotations

from django.db import migrations

WORDING_VERSION = "2026-01"

DEFAULT_TEMPLATES = [
    {
        "key": "appointment_reminder",
        "name": "Appointment reminder",
        "channel": "email",
        "purpose": "appointment_reminders",
        "locale": "en",
        "subject": "Appointment reminder",
        "body": (
            "Hi {first_name}, this is a reminder of your appointment on {date} "
            "at {time} with {provider}. Reply STOP to opt out."
        ),
        "variables": ["first_name", "date", "time", "provider"],
        "approval_status": "approved",
        "is_active": True,
    },
    {
        "key": "recall_reminder",
        "name": "Recall reminder",
        "channel": "sms",
        "purpose": "recall",
        "locale": "en",
        "subject": "",
        "body": (
            "Hi {first_name}, it is time for your {recall_name} check-up at "
            "{clinic}. Call us to book."
        ),
        "variables": ["first_name", "recall_name", "clinic"],
        "approval_status": "approved",
        "is_active": True,
    },
    {
        "key": "cancellation_confirmation",
        "name": "Cancellation confirmation",
        "channel": "email",
        "purpose": "transactional",
        "locale": "en",
        "subject": "Appointment cancelled",
        "body": (
            "Hi {first_name}, your appointment on {date} at {time} has been "
            "cancelled. Reply STOP to opt out."
        ),
        "variables": ["first_name", "date", "time"],
        "approval_status": "approved",
        "is_active": True,
    },
]


def seed_templates(apps, schema_editor):
    Clinic = apps.get_model("clinics", "Clinic")
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    for clinic in Clinic.objects.all().iterator():
        for spec in DEFAULT_TEMPLATES:
            exists = MessageTemplate.objects.filter(
                clinic_id=clinic.id, key=spec["key"]
            ).exists()
            if not exists:
                MessageTemplate.objects.create(clinic_id=clinic.id, version=1, **spec)


def unseed_templates(apps, schema_editor):
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    keys = [spec["key"] for spec in DEFAULT_TEMPLATES]
    MessageTemplate.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
