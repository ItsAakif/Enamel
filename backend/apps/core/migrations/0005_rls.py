"""Enable PostgreSQL row-level security on every tenant table (T135).

The policy set is defence in depth below ``TenantManager``/``ClinicScope``:

* ``clinic_isolation`` matches ``clinic_id`` against
  ``app.current_clinic_id`` (set with ``SET LOCAL`` by the request middleware
  and by ``core.tenancy.with_clinic``).
* ``clinic_bypass`` is the single, explicit maintenance escape hatch gated on
  ``app.bypass_rls = 'on'``. Migrations and management commands set it; request
  and job code never do.
* ``clinic_platform_scope`` exists only on tables whose ``clinic_id`` is
  nullable (audit, jobs, idempotency and a few platform-owned ML rows). It
  exposes ``clinic_id IS NULL`` rows only when no clinic context is active, so
  login/audit events and platform jobs stay reachable without opening tenant
  rows.

Tables are discovered from ``pg_catalog`` at migration time, so future tenant
tables are covered explicitly by adding this migration to their dependency
chain. Tables without a ``clinic_id`` column (``clinics_clinic``,
``accounts_user``, ``django_*``, ``auth_*``, ``token_blacklist`` ...) are out of
scope: they are either global catalogues or owned by a single principal and
carry no tenant discriminator.

The same migration installs the append-only trigger on ``core_auditevent``
(ARCHITECTURE.md section 10). Maintenance may still rewrite a row by opening
``bypass_rls()`` explicitly, which keeps the tamper-detection tests meaningful.
"""

from __future__ import annotations

from django.db import migrations


def _tenant_tables(connection) -> list[tuple[str, bool]]:
    """Return ``(table, clinic_id_is_nullable)`` for every tenant table."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, NOT a.attnotnull AS nullable
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_attribute AS a
              ON a.attrelid = c.oid
             AND a.attname = 'clinic_id'
             AND NOT a.attisdropped
            WHERE c.relkind = 'r'
              AND n.nspname = current_schema()
            ORDER BY c.relname
            """
        )
        return [(row[0], bool(row[1])) for row in cursor.fetchall()]


def _quote(table: str) -> str:
    return '"' + table.replace('"', '""') + '"'


def _policy_sql(table: str, nullable: bool) -> list[str]:
    quoted = _quote(table)
    current = "nullif(current_setting('app.current_clinic_id', true), '')::uuid"
    bypass = "current_setting('app.bypass_rls', true) = 'on'"

    statements = [
        f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS clinic_isolation ON {quoted}",
        f"DROP POLICY IF EXISTS clinic_bypass ON {quoted}",
        f"DROP POLICY IF EXISTS clinic_platform_scope ON {quoted}",
        (
            f"CREATE POLICY clinic_isolation ON {quoted} FOR ALL TO PUBLIC "
            f"USING (clinic_id = {current}) WITH CHECK (clinic_id = {current})"
        ),
        (
            f"CREATE POLICY clinic_bypass ON {quoted} FOR ALL TO PUBLIC "
            f"USING ({bypass}) WITH CHECK ({bypass})"
        ),
    ]
    if nullable:
        platform = f"clinic_id IS NULL AND {current} IS NULL"
        statements.append(
            f"CREATE POLICY clinic_platform_scope ON {quoted} FOR ALL TO PUBLIC "
            f"USING ({platform}) WITH CHECK ({platform})"
        )
    return statements


AUDIT_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION enamel_audit_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_setting('app.bypass_rls', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'core_auditevent is append-only'
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DROP TRIGGER IF EXISTS core_auditevent_immutable ON core_auditevent;
CREATE TRIGGER core_auditevent_immutable
BEFORE UPDATE OR DELETE ON core_auditevent
FOR EACH ROW EXECUTE FUNCTION enamel_audit_immutable();
"""

AUDIT_TRIGGER_REVERSE_SQL = """
DROP TRIGGER IF EXISTS core_auditevent_immutable ON core_auditevent;
DROP FUNCTION IF EXISTS enamel_audit_immutable();
"""


def enable_rls(apps, schema_editor) -> None:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.bypass_rls', 'on', true)")

    for table, nullable in _tenant_tables(connection):
        with connection.cursor() as cursor:
            for statement in _policy_sql(table, nullable):
                cursor.execute(statement)

    with connection.cursor() as cursor:
        cursor.execute(AUDIT_TRIGGER_SQL)


def disable_rls(apps, schema_editor) -> None:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.bypass_rls', 'on', true)")

    with connection.cursor() as cursor:
        cursor.execute(AUDIT_TRIGGER_REVERSE_SQL)

    for table, _nullable in _tenant_tables(connection):
        quoted = _quote(table)
        with connection.cursor() as cursor:
            cursor.execute(f"DROP POLICY IF EXISTS clinic_isolation ON {quoted}")
            cursor.execute(f"DROP POLICY IF EXISTS clinic_bypass ON {quoted}")
            cursor.execute(f"DROP POLICY IF EXISTS clinic_platform_scope ON {quoted}")
            cursor.execute(f"ALTER TABLE {quoted} NO FORCE ROW LEVEL SECURITY")
            cursor.execute(f"ALTER TABLE {quoted} DISABLE ROW LEVEL SECURITY")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_jobrequest"),
        ("ai", "0003_knowledgedocument_knowledgechunk_retrievallog_and_more"),
        ("analytics", "0001_initial"),
        ("billing", "0002_initial"),
        ("booking", "0001_initial"),
        ("charting", "0002_seed_conditions"),
        ("clinics", "0002_provider"),
        ("imaging", "0002_inferencejob"),
        ("interop", "0001_initial"),
        ("marketing", "0001_initial"),
        ("messaging", "0002_seed_templates"),
        ("ml", "0001_initial"),
        ("patients", "0002_patient_search_index"),
        ("plans", "0002_seed_consent_templates"),
        ("portal", "0001_initial"),
        ("rcm", "0001_initial"),
        ("scheduling", "0003_waitlist"),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
