"""Seed the global condition-code catalogue (T108).

Idempotent by construction: ``update_or_create`` keyed on ``code`` means
re-running the migration (or applying it to a database that already has some
rows) converges on the same 14 codes instead of duplicating them. The seed list
itself lives in ``apps.charting.conditions`` so the migration, the tests and any
future runtime re-seed read the same source.
"""

from __future__ import annotations

from django.db import migrations


def seed_conditions(apps, schema_editor):
    from apps.charting.conditions import apply_condition_seeds

    ConditionCode = apps.get_model("charting", "ConditionCode")
    apply_condition_seeds(ConditionCode)


def unseed_conditions(apps, schema_editor):
    from apps.charting.conditions import CONDITION_CODES

    ConditionCode = apps.get_model("charting", "ConditionCode")
    ConditionCode.objects.filter(code__in=CONDITION_CODES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("charting", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_conditions, unseed_conditions),
    ]
