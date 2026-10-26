"""The test fixture role (T138).

The shipped test suite asserts on **unscoped** tenant reads - "the audit
rows of this clinic", "the patients of this clinic" without a clinic context.
Under the runtime role those reads are empty by design (that is what the
row-level policies do), so the suite cannot run as the runtime role without
either weakening the policies or rewriting a hundred test files.

This module provides the third option: a **test fixture role**.

``enamel_test``
    ``LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE``. It is a member
    of **no** role (neither a runtime role nor the owner), it holds no bypass
    of any kind, and it is never referenced by the compose stack. It exists
    only inside a pytest test database, it is created by the harness
    (``backend/scripts/prepare_rls_test_db.py``) or by the fixture in
    :mod:`backend.apps.core.tests.test_rls_test_role`, and it connects only
    through ``DATABASE_URL``.

The role carries the same table grants as the runtime role **plus one
clearly-named, test-only visibility policy** (``test_fixture_visibility``) so
the suite can set up and read its fixtures. That policy is granted to
``enamel_test`` and to nothing else, so it can never widen a runtime role.

What the suite under this role proves, and what it does not, is stated in
the migration and gate documentation. The short version: the
suite under the fixture role proves that the **code paths** work under a
non-superuser role with the runtime grants; the boundary proofs
(``apps/core/tests/test_rls*.py``, the number is the release gate's floor in
``apps/core/tests/rls_proof_floor.py``) are the isolation proof, and they run
against the **real runtime roles** with the real policies in force.
"""

from __future__ import annotations

from typing import Any

#: The test fixture role.
TEST_ROLE = "enamel_test"

#: The test-only visibility policy. It is granted to the test role and to
#: nothing else.
TEST_POLICY = "test_fixture_visibility"

#: Every runtime role of ADR-013. The test role must not be a member of any
#: of them, and none of them may hold the test policy.
RUNTIME_ROLES: tuple[str, ...] = (
    "enamel_app",
    "enamel_worker",
    "enamel_platform",
    "enamel_portal",
    "enamel_portal_auth",
    "enamel_maint",
)


def _quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def tenant_tables(cursor) -> list[str]:
    """Every table that carries a ``clinic_id`` (the RLS-protected ones)."""

    cursor.execute(
        """
        SELECT c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_attribute AS a
          ON a.attrelid = c.oid
         AND a.attname = 'clinic_id'
         AND NOT a.attisdropped
        WHERE c.relkind = 'r' AND n.nspname = current_schema()
        ORDER BY c.relname
        """
    )
    return [row[0] for row in cursor.fetchall()]


def ensure_test_role(connection: Any) -> None:
    """Create the test fixture role, its grants and its visibility policy.

    Idempotent. The role is created in the **current database** only, and the
    policies are dropped and recreated so a stale policy set cannot survive a
    schema change.
    """

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [TEST_ROLE])
        if cursor.fetchone() is None:
            cursor.execute(
                f'CREATE ROLE "{TEST_ROLE}" LOGIN NOSUPERUSER NOBYPASSRLS '
                f"NOCREATEDB NOCREATEROLE"
            )
        cursor.execute(
            f'ALTER ROLE "{TEST_ROLE}" LOGIN NOSUPERUSER NOBYPASSRLS '
            f"NOCREATEDB NOCREATEROLE"
        )
        # The same table grants as the runtime role, plus TRUNCATE so the
        # pytest-django teardown can flush the database.
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + _quote(TEST_ROLE))
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES "
            "IN SCHEMA public TO " + _quote(TEST_ROLE)
        )
        cursor.execute(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO " + _quote(TEST_ROLE)
        )
        for table in tenant_tables(cursor):
            cursor.execute(
                f"DROP POLICY IF EXISTS {_quote(TEST_POLICY)} ON {_quote(table)}"
            )
            cursor.execute(
                f'CREATE POLICY "{TEST_POLICY}" ON "{table}" FOR ALL TO "{TEST_ROLE}" '
                f"USING (true) WITH CHECK (true)"
            )
        # The audit chain is read through the owner-level helper, so the test
        # role needs the same EXECUTE privilege the runtime roles have.
        cursor.execute("SELECT to_regprocedure('enamel_audit_prev_hash(uuid)')")
        if cursor.fetchone() is not None:
            cursor.execute(
                "GRANT EXECUTE ON FUNCTION enamel_audit_prev_hash(uuid) TO " + _quote(TEST_ROLE)
            )


def role_attributes(cursor, role: str) -> dict[str, bool]:
    cursor.execute(
        "SELECT rolsuper, rolbypassrls, rolcanlogin, rolcreatedb, rolcreaterole "
        "FROM pg_roles WHERE rolname = %s",
        [role],
    )
    row = cursor.fetchone()
    if row is None:
        return {}
    return {
        "superuser": bool(row[0]),
        "bypassrls": bool(row[1]),
        "login": bool(row[2]),
        "createdb": bool(row[3]),
        "createrole": bool(row[4]),
    }


def memberships(cursor, role: str) -> set[str]:
    """The roles ``role`` is a member of."""

    cursor.execute(
        "SELECT granted.rolname FROM pg_auth_members AS m "
        "JOIN pg_roles AS member ON member.oid = m.member "
        "JOIN pg_roles AS granted ON granted.oid = m.roleid "
        "WHERE member.rolname = %s",
        [role],
    )
    return {row[0] for row in cursor.fetchall()}


def policies_for_role(cursor, role: str) -> set[str]:
    """The policy names that are granted to ``role``."""

    cursor.execute(
        "SELECT policyname FROM pg_policies WHERE schemaname = current_schema() "
        "AND roles::text LIKE %s",
        [f"%{role}%"],
    )
    return {row[0] for row in cursor.fetchall()}


__all__ = [
    "RUNTIME_ROLES",
    "TEST_POLICY",
    "TEST_ROLE",
    "ensure_test_role",
    "memberships",
    "policies_for_role",
    "role_attributes",
    "tenant_tables",
]
