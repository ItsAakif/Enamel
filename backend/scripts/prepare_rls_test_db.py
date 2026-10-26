#!/usr/bin/env python
"""Prepare the test database for a run under a non-superuser role (T138).

The runtime role has no ``CREATEDB``, so the test database cannot be created
by the test run itself. This script does what the owner does before the run:

1. the owner creates the test database;
2. the owner migrates it (the migrations install the roles and the
   role-scoped policies);
3. the owner grants the runtime role its privileges;
4. the owner hands the table ownership to the connecting role, because the
   test teardown truncates every table and the identity sequences are linked
   to their table;
5. when the connecting role is the **test fixture role** (``enamel_test``),
   the owner also installs that role and its one test-only visibility
   policy.

Afterwards the suite runs with the role::

    DATABASE_URL=postgresql://enamel_app:...@127.0.0.1:5433/enamel \
        python -m pytest -q --reuse-db

The environment variables ``ENAMEL_OWNER_DATABASE_URL`` and
``ENAMEL_RUNTIME_DATABASE_URL`` override the defaults, and
``ENAMEL_RUNTIME_ROLE`` selects the role the suite connects as. The defaults
are the **local rehearsal** credentials of the compose stack - the same ones
as ``backend/config/settings/test.py`` - and are not a production secret.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

OWNER_URL = os.environ.get(
    "ENAMEL_OWNER_DATABASE_URL",
    "postgresql://enamel:enamel_dev_password@127.0.0.1:5433/enamel",
)
RUNTIME_URL = os.environ.get(
    "ENAMEL_RUNTIME_DATABASE_URL",
    "postgresql://enamel_app:enamel_dev_password@127.0.0.1:5433/enamel",
)
DB_NAME = os.environ.get("ENAMEL_TEST_DB_NAME", "test_enamel")
RUNTIME_ROLE = os.environ.get("ENAMEL_RUNTIME_ROLE", "enamel_app")


def _owner_test_url() -> str:
    return OWNER_URL.rsplit("/", 1)[0] + "/" + DB_NAME


def _password(url: str) -> str:
    return url.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]


def main() -> int:
    if RUNTIME_ROLE == "enamel_test":
        print("== 0. the owner creates the test fixture role ==")
        with psycopg.connect(OWNER_URL, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [RUNTIME_ROLE])
            if cur.fetchone() is None:
                cur.execute(
                    f'CREATE ROLE "{RUNTIME_ROLE}" LOGIN NOSUPERUSER NOBYPASSRLS '
                    f"NOCREATEDB NOCREATEROLE"
                )
            cur.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS "
                    "NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(RUNTIME_ROLE), sql.Literal(_password(RUNTIME_URL)))
            )

    print(f"== 1. the owner creates the test database '{DB_NAME}' ==")
    with psycopg.connect(OWNER_URL, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{DB_NAME}" OWNER enamel')
        cur.execute(f'GRANT CONNECT, TEMPORARY ON DATABASE "{DB_NAME}" TO {RUNTIME_ROLE}')

    print("== 2. the owner migrates it ==")
    env = dict(os.environ, DATABASE_URL=_owner_test_url())
    subprocess.run(
        [sys.executable, str(BACKEND / "manage.py"), "migrate"],
        check=True,
        env=env,
        cwd=str(BACKEND),
    )

    print(f"== 3. the owner grants '{RUNTIME_ROLE}' its privileges ==")
    with psycopg.connect(_owner_test_url(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {RUNTIME_ROLE}")
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES "
            f"IN SCHEMA public TO {RUNTIME_ROLE}"
        )
        cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}")

        print(f"== 4. the owner hands the table ownership to '{RUNTIME_ROLE}' ==")
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = 'public'"
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(f'ALTER TABLE "{table}" OWNER TO {RUNTIME_ROLE}')

    if RUNTIME_ROLE == "enamel_test":
        print("== 5. the owner installs the test-only visibility policy ==")
        from apps.core.tests.rls_test_role import ensure_test_role

        with psycopg.connect(_owner_test_url(), autocommit=True) as conn:
            ensure_test_role(conn)

    print(f"== done ({len(tables)} tables) ==")
    print(f"   DATABASE_URL={RUNTIME_URL} python -m pytest -q --reuse-db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
