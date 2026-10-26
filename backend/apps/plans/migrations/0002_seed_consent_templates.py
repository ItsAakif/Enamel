"""Seed the default consent templates (T117).

The template list is append-only: never edit an existing entry in place, because
this migration must stay reproducible. Bump a template through the API instead
(which creates version N+1). New clinics get the same templates through
``apps.plans.seed.ensure_default_consent_templates``.
"""

from __future__ import annotations

from django.db import migrations

DEFAULT_CONSENT_TEMPLATES = [
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
        "approval_state": "approved",
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
        "approval_state": "approved",
        "is_active": True,
    },
]


def seed_consent_templates(apps, schema_editor):
    Clinic = apps.get_model("clinics", "Clinic")
    ConsentTemplate = apps.get_model("plans", "ConsentTemplate")
    for clinic in Clinic.objects.all().iterator():
        for spec in DEFAULT_CONSENT_TEMPLATES:
            exists = ConsentTemplate.objects.filter(
                clinic_id=clinic.id, key=spec["key"]
            ).exists()
            if not exists:
                ConsentTemplate.objects.create(clinic_id=clinic.id, version=1, **spec)


def unseed_consent_templates(apps, schema_editor):
    ConsentTemplate = apps.get_model("plans", "ConsentTemplate")
    keys = [spec["key"] for spec in DEFAULT_CONSENT_TEMPLATES]
    ConsentTemplate.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("plans", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_consent_templates, unseed_consent_templates),
    ]
