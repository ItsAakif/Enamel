"""The database role topology of ADR-013 (T137).

The policy inventory of ``core/migrations/0006_principal_scoped_rls.py`` is
role-scoped, so the roles themselves are part of the security model. This
module asserts the topology:

* the application role is not a superuser, has no ``BYPASSRLS``, no
  ``CREATEDB`` and no ``CREATEROLE``;
* it is not a member of the maintenance role (the only non-owner role that
  holds the bypass) and not a member of the owner;
* the worker inherits the staff policies and the cross-clinic job access of
  the platform group;
* the patient roles have no grant on the staff-only tables.

The module is part of the ``rls`` marker: it is a release gate, so it fails
loudly instead of skipping.
"""

from __future__ import annotations

import pytest
from django.db import connection

pytestmark = [pytest.mark.django_db, pytest.mark.rls]


APP_ROLE = "enamel_app"
WORKER_ROLE = "enamel_worker"
PLATFORM_ROLE = "enamel_platform"
PORTAL_ROLE = "enamel_portal"
PORTAL_AUTH_ROLE = "enamel_portal_auth"
MAINT_ROLE = "enamel_maint"

#: The roles and the login attribute they carry.
EXPECTED_ROLES: dict[str, bool] = {
    APP_ROLE: True,
    WORKER_ROLE: True,
    PLATFORM_ROLE: False,
    PORTAL_ROLE: False,
    PORTAL_AUTH_ROLE: False,
    MAINT_ROLE: True,
}

#: The tables a patient role may never touch.
STAFF_ONLY_TABLES = (
    "clinics_membership",
    "core_auditevent",
    "charting_toothstate",
)


def role_attributes(role: str) -> dict:
    with connection.cursor() as cursor:
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


def memberships(role: str) -> set[str]:
    """The roles ``role`` is a **directly** member of."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT granted.rolname FROM pg_auth_members AS m "
            "JOIN pg_roles AS member ON member.oid = m.member "
            "JOIN pg_roles AS granted ON granted.oid = m.roleid "
            "WHERE member.rolname = %s",
            [role],
        )
        return {row[0] for row in cursor.fetchall()}


def has_role(role: str, target: str) -> bool:
    """True when ``role`` is a member of ``target``, directly **or** through a
    chain of memberships.

    PostgreSQL membership is transitive, so a direct-membership query can
    miss an escalation path: ``enamel_worker`` inherits ``enamel_app``, and
    ``enamel_app`` is a member of ``enamel_portal`` - one hop away from the
    owner. ``pg_has_role(..., 'MEMBER')`` is the transitive form and is what
    the escalation inventory uses (F-06).
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_has_role(%s, %s, 'MEMBER')",
            [role, target],
        )
        row = cursor.fetchone()
    return bool(row and row[0])


def owner_role() -> str:
    """The role that owns the tenant tables (the compose owner)."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE oid = 'patients_patient'::regclass"
        )
        row = cursor.fetchone()
    assert row and row[0], "the tenant table has no owner"
    return row[0]


def table_privileges(role: str, table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = %s AND table_name = %s",
            [role, table],
        )
        return {row[0] for row in cursor.fetchall()}


def test_every_role_exists_with_the_expected_attributes():
    for role, can_login in EXPECTED_ROLES.items():
        attributes = role_attributes(role)
        assert attributes, f"the role {role} does not exist"
        assert attributes["login"] is can_login, role
        assert attributes["superuser"] is False, role
        assert attributes["bypassrls"] is False, role
        assert attributes["createdb"] is False, role
        assert attributes["createrole"] is False, role


def test_the_application_role_is_not_a_member_of_a_bypass_holder():
    granted = memberships(APP_ROLE)
    assert MAINT_ROLE not in granted, granted
    # The owner is resolved from the catalog, not from a literal name: on a
    # database whose owner is named differently the literal assertion would be
    # vacuous (N-05).
    assert owner_role() not in granted, granted
    assert granted == {PORTAL_ROLE, PORTAL_AUTH_ROLE}, granted


def test_no_runtime_role_is_transitively_a_member_of_the_owner_or_the_bypass():
    """The escalation inventory is transitive (F-06).

    ``pg_has_role(..., 'MEMBER')`` follows the whole membership chain, so a
    path that is one hop away - ``enamel_worker`` -> ``enamel_app`` ->
    ``enamel_portal`` - is caught even when the direct query sees nothing.
    """

    owner = owner_role()
    for role in (APP_ROLE, WORKER_ROLE, PLATFORM_ROLE, PORTAL_ROLE, PORTAL_AUTH_ROLE):
        assert not has_role(role, MAINT_ROLE), (
            f"{role} is a member of the bypass holder {MAINT_ROLE}"
        )
        assert not has_role(role, owner), (
            f"{role} is a member of the owner {owner}"
        )


def test_the_transitive_check_sees_what_the_direct_one_misses():
    """The proof that the transitive form is the stronger one.

    ``enamel_worker`` is a member of ``enamel_app``, and ``enamel_app`` is a
    member of ``enamel_portal``. The direct query does not see that edge; the
    transitive one does.
    """

    assert PORTAL_ROLE not in memberships(WORKER_ROLE), (
        "the fixture topology changed: the direct edge is no longer missing, "
        "so this proof no longer shows the difference"
    )
    assert has_role(WORKER_ROLE, PORTAL_ROLE), (
        "pg_has_role does not follow the membership chain"
    )


def test_the_worker_inherits_the_staff_policies_and_the_platform_group():
    granted = memberships(WORKER_ROLE)
    assert APP_ROLE in granted, granted
    assert PLATFORM_ROLE in granted, granted
    assert MAINT_ROLE not in granted, granted


def test_the_platform_group_is_not_a_member_of_the_bypass_holder():
    granted = memberships(PLATFORM_ROLE)
    assert MAINT_ROLE not in granted, granted
    # The owner comes from the catalog (N-05).
    assert owner_role() not in granted, granted


def test_the_patient_roles_have_no_grant_on_the_staff_only_tables():
    """A patient role may append an audit row but never read the audit chain."""

    for role in (PORTAL_ROLE, PORTAL_AUTH_ROLE):
        for table in STAFF_ONLY_TABLES:
            privileges = table_privileges(role, table)
            reading = privileges & {"SELECT", "UPDATE", "DELETE"}
            assert not reading, f"{role} may read {table}: {sorted(reading)}"
    # The membership table and the clinical notes are out of reach entirely.
    for role in (PORTAL_ROLE, PORTAL_AUTH_ROLE):
        assert not table_privileges(role, "clinics_membership"), role
        assert not table_privileges(role, "charting_toothstate"), role


def test_the_portal_role_may_reach_the_patient_tables_it_needs():
    privileges = table_privileges(PORTAL_ROLE, "patients_patient")
    assert "SELECT" in privileges
    assert "DELETE" not in privileges
    privileges = table_privileges(PORTAL_ROLE, "clinics_membership")
    assert not privileges


def test_the_bootstrap_role_may_not_reach_a_clinical_table():
    privileges = table_privileges(PORTAL_AUTH_ROLE, "scheduling_appointment")
    assert not privileges
    privileges = table_privileges(PORTAL_AUTH_ROLE, "billing_invoice")
    assert not privileges
    privileges = table_privileges(PORTAL_AUTH_ROLE, "portal_portalotpchallenge")
    assert {"SELECT", "INSERT", "UPDATE"} <= privileges


def test_the_maintenance_role_is_the_only_non_owner_bypass_holder():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'patients_patient'::regclass"
        )
        owner = cursor.fetchone()[0]
        cursor.execute(
            "SELECT policyname, roles::text FROM pg_policies "
            "WHERE schemaname = current_schema() AND policyname = 'clinic_bypass'"
        )
        rows = cursor.fetchall()

    assert rows
    for name, roles in rows:
        assert MAINT_ROLE in roles, name
        # The other holder is the role that owned the table when the migration
        # ran (the compose owner). No runtime role and no test fixture role
        # may hold the bypass.
        others = set(roles.replace("{", "").replace("}", "").split(",")) - {MAINT_ROLE}
        assert len(others) == 1, (name, roles)
        other = others.pop()
        assert other not in (
            APP_ROLE,
            WORKER_ROLE,
            PLATFORM_ROLE,
            PORTAL_ROLE,
            PORTAL_AUTH_ROLE,
            "enamel_test",
        ), (name, other)
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", [other])
            other_row = cursor.fetchone()
        assert other == owner or bool(other_row and other_row[0]), (name, other, owner)


def test_the_table_owner_is_the_only_role_with_the_bypass_attribute():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolname LIKE 'enamel%' "
            "AND rolname <> 'enamel'"
        )
        offenders = [row[0] for row in cursor.fetchall()]
    assert not offenders, f"a non-owner role holds BYPASSRLS: {offenders}"
