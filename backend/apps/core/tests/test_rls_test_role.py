"""The test fixture role must never be able to widen a runtime role (T138).

``enamel_test`` exists so the shipped test suite can run under a
non-superuser role (see ``apps/core/tests/rls_test_role.py``). It is a
**fixture** role, and this module proves that it stays one:

* it is not a member of any runtime role, and no runtime role is a member of
  it;
* it holds no ``BYPASSRLS`` and no superuser, DDL or DCL right;
* no runtime role holds the test-only visibility policy;
* no runtime role holds the test role's extra grants (``TRUNCATE``);
* the compose stack never references it - the configuration is read and
  checked, so the check fails if the name ever appears there.

The module never skips: when the role is missing the fixture creates it (in
the test database only) and the assertions run against the real catalogue.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.db import connection

from apps.core.tests.rls_test_role import (
    RUNTIME_ROLES,
    TEST_POLICY,
    TEST_ROLE,
    ensure_test_role,
    memberships,
    policies_for_role,
    role_attributes,
)

pytestmark = [pytest.mark.django_db, pytest.mark.rls]

REPO_ROOT = Path(__file__).resolve().parents[4]

#: The deployment files that must never mention the test role.
DEPLOYMENT_FILES = (
    "deploy/docker-compose.yml",
    "deploy/.env.example",
    "deploy/db/init/01-enamel-app-role.sql",
    "deploy/apply-app-role.ps1",
    ".env.example",
)


@pytest.fixture(scope="module", autouse=True)
def _test_role(django_db_setup, django_db_blocker):
    """Make sure the test fixture role and its policy exist in this database.

    The owner installs them (the harness does it before a run under the test
    role; this fixture does it in the ordinary owner run). When the suite
    already runs as the test fixture role the role is there and nothing has
    to be installed.
    """

    from apps.core.tests.rls_owner_connection import connection_is_superuser

    with django_db_blocker.unblock():
        if connection_is_superuser():
            ensure_test_role(connection)


def test_the_test_role_exists_with_the_expected_attributes():
    with connection.cursor() as cursor:
        attributes = role_attributes(cursor, TEST_ROLE)

    assert attributes, f"the test fixture role {TEST_ROLE} does not exist"
    assert attributes["login"] is True
    assert attributes["superuser"] is False
    assert attributes["bypassrls"] is False
    assert attributes["createdb"] is False
    assert attributes["createrole"] is False


def test_the_test_role_is_not_a_member_of_any_runtime_role():
    with connection.cursor() as cursor:
        granted = memberships(cursor, TEST_ROLE)
        members = memberships(cursor, "enamel")

    assert not (granted & set(RUNTIME_ROLES)), granted
    # The owner role (the compose superuser) must not have it either: the
    # test role is never a member of anything.
    assert TEST_ROLE not in members, members


def test_no_runtime_role_is_a_member_of_the_test_role():
    with connection.cursor() as cursor:
        for role in RUNTIME_ROLES:
            assert TEST_ROLE not in memberships(cursor, role), role


def test_no_runtime_role_holds_the_test_visibility_policy():
    with connection.cursor() as cursor:
        for role in RUNTIME_ROLES:
            policies = policies_for_role(cursor, role)
            assert TEST_POLICY not in policies, role


def test_the_test_visibility_policy_is_granted_to_the_test_role_only():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename, roles::text FROM pg_policies "
            "WHERE schemaname = current_schema() AND policyname = %s",
            [TEST_POLICY],
        )
        rows = cursor.fetchall()

    assert rows, "the test visibility policy is not installed"
    for table, roles in rows:
        assert TEST_ROLE in roles, table
        for role in RUNTIME_ROLES:
            assert role not in roles, table


def test_no_runtime_role_holds_the_test_roles_extra_grants():
    """``TRUNCATE`` is the test role's own privilege; the runtime has none."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT grantee, count(*) FROM information_schema.role_table_grants "
            "WHERE table_schema = current_schema() AND privilege_type = 'TRUNCATE' "
            "GROUP BY grantee"
        )
        truncate_grantees = {row[0] for row in cursor.fetchall()}

    assert TEST_ROLE in truncate_grantees
    for role in RUNTIME_ROLES:
        assert role not in truncate_grantees, role


def test_the_compose_stack_never_references_the_test_role():
    for relative in DEPLOYMENT_FILES:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert TEST_ROLE not in text, f"{relative} references the test fixture role"
