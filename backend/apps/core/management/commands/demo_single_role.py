"""Demo-only: let the database owner read without a forced RLS policy.

The production stack runs the application as ``enamel_app`` and forces row
level security on every tenant table, so even the table owner cannot bypass
it. A single-role demo (the free Render/Supabase deployment) connects as the
database owner instead, and forced RLS would hide the principal-scoped rows
the login and portal bootstrap need.

This command turns ``FORCE ROW LEVEL SECURITY`` off for the tenant tables so
the owner bypasses the policies, and ``--restore`` turns it back on. The
application-layer tenant manager is untouched: every ORM query is still
scoped, and the role-scoped policies remain installed for the production
stack.

Never run this against a deployment that handles real patient data.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = (
        "Disable (or with --restore, re-enable) FORCE ROW LEVEL SECURITY on the "
        "tenant tables for a single-role demo deployment."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--restore",
            action="store_true",
            help="Re-enable FORCE ROW LEVEL SECURITY instead of disabling it.",
        )

    def handle(self, *args, **options) -> None:
        restore = bool(options["restore"])
        # PostgreSQL syntax: ENABLE/DISABLE ROW LEVEL SECURITY controls the
        # feature; FORCE/NO FORCE controls whether the table owner is subject
        # to it. We only toggle the FORCE flag and leave RLS enabled.
        force = "FORCE" if restore else "NO FORCE"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND EXISTS (
                      SELECT 1
                      FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = c.relname
                        AND col.column_name = 'clinic_id'
                  )
                ORDER BY c.relname
                """
            )
            tables = [row[0] for row in cursor.fetchall()]

            for table in tables:
                cursor.execute(f'ALTER TABLE "{table}" {force} ROW LEVEL SECURITY')

        if restore:
            self.stdout.write(
                self.style.SUCCESS(
                    f"FORCE ROW LEVEL SECURITY re-enabled on {len(tables)} tenant tables."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "FORCE ROW LEVEL SECURITY disabled on "
                    f"{len(tables)} tenant tables. This is for a single-role demo "
                    "only; the application layer remains tenant-scoped. Run with "
                    "--restore before using the database with the hardened stack."
                )
            )
