"""Trigram search indexes for patients (T106).

Patient search matches partial names, phone digits and email with
``icontains`` (``LIKE '%term%'``). A btree index cannot serve a leading
wildcard, so the measured choice recorded in ARCHITECTURE.md section 7 is a
``pg_trgm`` GIN index on each searchable column. ``patients_patient`` stays small
enough that the plain btree indexes from ``0001_initial`` still handle the
clinic/ordering paths.

The extension is created here rather than in ``core``'s extension migration
because it is only needed by this app. ``CREATE EXTENSION IF NOT EXISTS`` is
idempotent and ``pg_trgm`` is a trusted extension, so it can be created by the
database owner without superuser rights.

The migration is intentionally outside the model state (``RunSQL``), so
``makemigrations`` reports no drift. Reversing drops the indexes but leaves the
extension alone: other schemas may own objects that use its operators.
"""

from __future__ import annotations

from django.db import migrations

CREATE_SQL = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS pat_first_name_trgm_idx "
    "ON patients_patient USING gin (first_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS pat_last_name_trgm_idx "
    "ON patients_patient USING gin (last_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS pat_phone_trgm_idx "
    "ON patients_patient USING gin (phone_e164 gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS pat_email_trgm_idx "
    "ON patients_patient USING gin (email gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS pat_number_trgm_idx "
    "ON patients_patient USING gin (number gin_trgm_ops)",
]

DROP_SQL = [
    "DROP INDEX IF EXISTS pat_number_trgm_idx",
    "DROP INDEX IF EXISTS pat_email_trgm_idx",
    "DROP INDEX IF EXISTS pat_phone_trgm_idx",
    "DROP INDEX IF EXISTS pat_last_name_trgm_idx",
    "DROP INDEX IF EXISTS pat_first_name_trgm_idx",
]


class Migration(migrations.Migration):
    dependencies = [
        ("patients", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=CREATE_SQL, reverse_sql=DROP_SQL),
    ]
