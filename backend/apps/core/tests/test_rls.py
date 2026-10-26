"""Database-level tenant isolation (T135, T137).

These tests exercise the PostgreSQL policies directly, not just
``TenantManager``.  The policies of ``core/migrations/0006_principal_scoped_rls.py``
are **role-scoped**: every policy names the role it is written for, so the
proofs switch the connection to the real role (``enamel_app`` for the staff
runtime, ``enamel_portal`` for the patient portal, ``enamel_portal_auth`` for
the one-time-code bootstrap and ``enamel_maint`` for the maintenance
commands) and assert what that role may and may not see.

The module is the release gate of the deny-by-default matrix of ADR-013
section 9 plus the negative tests of the role split.  It never skips: when the
environment cannot run the proofs (no superuser connection, no roles) the
module **fails** with the reason, because a skip has the same exit code as a
pass.  ``ENAMEL_REQUIRE_RLS=1`` (set by ``scripts/check_rls.py``) keeps the
switch visible for the operators.

The whole module carries the ``rls`` marker, so it can be selected on its own
(``pytest -m rls``).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest
from django.db import DatabaseError, connection, transaction

from apps.core.rls import (
    bypass_rls,
    rls_context,
)
from apps.core.tenancy import UnscopedQueryError, with_clinic
from apps.core.tests.rls_owner_connection import owner_connection  # noqa: F401  (the boundary proofs need a role switch)
from apps.patients.models import Patient

pytestmark = [pytest.mark.django_db, pytest.mark.rls]


#: The roles of the ADR-013 topology.
APP_ROLE = "enamel_app"
WORKER_ROLE = "enamel_worker"
PLATFORM_ROLE = "enamel_platform"
PORTAL_ROLE = "enamel_portal"
PORTAL_AUTH_ROLE = "enamel_portal_auth"
MAINT_ROLE = "enamel_maint"

#: When this is truthy the module must not skip: a missing superuser
#: connection is a gate failure, not a "cannot run here" condition.
REQUIRE_RLS_VALUES = frozenset({"1", "true", "yes", "on"})


def require_rls() -> bool:
    """True when the release gate demands the proofs instead of skipping them.

    Read at call time so a test (or a CI job) can flip the switch with the
    environment variable.
    """

    return os.environ.get("ENAMEL_REQUIRE_RLS", "").strip().lower() in REQUIRE_RLS_VALUES


_NO_SUPERUSER_REASON = (
    "RLS policy tests require a superuser test connection: PostgreSQL always "
    "lets a superuser bypass row-level security, and the proofs switch the "
    "connection to the real non-superuser roles. Run them against the compose "
    "database (the 'enamel' role is a superuser there)."
)

_NO_ROLE_REASON = (
    "The RLS roles are missing. Run 'manage.py migrate core' (migration "
    "0006_principal_scoped_rls creates them) before the policy proofs."
)


@pytest.fixture(scope="module", autouse=True)
def _require_role_privileges(django_db_setup, django_db_blocker, owner_connection):  # noqa: F811
    """The policy proof needs a role that can switch to the non-superuser roles.

    The module never skips: a missing privilege is a gate failure. The
    connection is the compose owner in the ordinary run; when the suite runs
    under the test fixture role the shared ``owner_connection`` fixture
    reconnects it with the owner's credentials first.
    """

    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            row = cursor.fetchone()
            if not (row and row[0]):
                pytest.fail(_NO_SUPERUSER_REASON)
            cursor.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                [[APP_ROLE, WORKER_ROLE, PLATFORM_ROLE, PORTAL_ROLE, PORTAL_AUTH_ROLE, MAINT_ROLE]],
            )
            present = {row[0] for row in cursor.fetchall()}
    missing = sorted(
        {APP_ROLE, WORKER_ROLE, PLATFORM_ROLE, PORTAL_ROLE, PORTAL_AUTH_ROLE, MAINT_ROLE}
        - present
    )
    if missing:
        pytest.fail(f"{_NO_ROLE_REASON} Missing: {', '.join(missing)}.")


@contextmanager
def as_role(role: str):
    """Run the block as ``role`` (the connection is the table owner).

    The switch is asserted: the proof must really run under the named
    runtime role, with that role's policies in force. A test role or a
    session-level bypass could otherwise mask a leak.
    """

    with connection.cursor() as cursor:
        cursor.execute(f'SET ROLE "{role}"')
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        current, session = cursor.fetchone()
    assert current == role, f"the proof did not run as {role} (it ran as {current})"
    assert session != role, f"the session user is already {role}"
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
        except Exception:  # pragma: no cover - transaction already aborted
            pass


def make_patient(clinic, number: str, first: str) -> Patient:
    return Patient.all_objects.create(
        clinic=clinic,
        number=number,
        first_name=first,
        last_name="Isolation",
    )


def tenant_tables() -> list[str]:
    with connection.cursor() as cursor:
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


def policy_rows() -> list[tuple[str, str, str, str]]:
    """``(table, policy, roles, command)`` for every policy of the schema."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT tablename, policyname, roles::text, cmd
            FROM pg_policies
            WHERE schemaname = current_schema()
            ORDER BY tablename, policyname
            """
        )
        return [(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]


# --- the policy inventory ---------------------------------------------------


def test_every_tenant_table_has_forced_rls_and_the_role_scoped_policies(db):
    """Every tenant table is protected and every policy names its role."""

    tables = tenant_tables()
    assert tables, "no tenant tables discovered"

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r'
              AND n.nspname = current_schema()
              AND c.relrowsecurity
              AND c.relforcerowsecurity
            """
        )
        forced = {row[0] for row in cursor.fetchall()}

    assert set(tables) <= forced

    policies = policy_rows()
    by_table: dict[str, set[str]] = {}
    for table, name, _roles, _cmd in policies:
        by_table.setdefault(table, set()).add(name)

    for table in tables:
        names = by_table.get(table, set())
        assert "clinic_bypass" in names, f"{table} has no bypass policy"
        if table in ("portal_patientsession", "portal_portalotpchallenge"):
            # The two portal-owned tables are addressed by principal only.
            assert "clinic_isolation" not in names, table
        else:
            assert "clinic_isolation" in names, f"{table} has no tenant policy"


def test_no_policy_is_created_for_public(db):
    """``TO PUBLIC`` would apply the staff policies to every role (ADR-013)."""

    offenders = [row for row in policy_rows() if "public" in row[2].lower()]
    assert not offenders, f"policies are still granted to PUBLIC: {offenders}"


def test_the_bypass_policy_is_granted_to_the_owner_and_the_maintenance_role(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'patients_patient'::regclass"
        )
        owner = cursor.fetchone()[0]

    roles = {
        name: role_list
        for _table, name, role_list, _cmd in policy_rows()
        if name == "clinic_bypass"
    }
    assert roles, "no bypass policy found"
    for name, role_list in roles.items():
        assert MAINT_ROLE in role_list, name
        # The other holder is the role that owned the table when the migration
        # ran (the compose owner). It is never a runtime role and never the
        # test fixture role.
        others = set(role_list.replace("{", "").replace("}", "").split(",")) - {
            MAINT_ROLE
        }
        assert len(others) == 1, (name, role_list)
        other = others.pop()
        assert other not in (APP_ROLE, WORKER_ROLE, PLATFORM_ROLE, PORTAL_ROLE, PORTAL_AUTH_ROLE)
        assert other != "enamel_test"
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", [other])
            other_row = cursor.fetchone()
        assert other == owner or bool(other_row and other_row[0]), (name, other, owner)


def test_the_two_portal_tables_do_not_carry_the_tenant_policy(db):
    """A portal request may never read another patient's session row."""

    policies = policy_rows()
    for table in ("portal_patientsession", "portal_portalotpchallenge"):
        names = {name for row_table, name, _roles, _cmd in policies if row_table == table}
        assert "clinic_isolation" not in names, table
        assert "clinic_bypass" in names, table
        assert any(name.startswith("principal_") for name in names), table


# --- the deny-by-default matrix (ADR-013 section 9) -------------------------


def test_a_tenant_reads_no_row_of_another_tenant(clinic_factory):
    first = clinic_factory(slug="rls-read-a")
    second = clinic_factory(slug="rls-read-b")
    own = make_patient(first, "A-1", "Ada")
    make_patient(second, "B-1", "Bao")

    with as_role(APP_ROLE), with_clinic(first):
        assert list(Patient.objects.all()) == [own]
        assert set(Patient.all_objects.values_list("id", flat=True)) == {own.id}


def test_a_cross_tenant_write_is_rejected(clinic_factory):
    """The database refuses a row of another clinic, not only the ORM.

    The application layer already refuses the write (``CrossClinicWriteError``),
    so the proof is a raw statement: the row-level policy has to reject it on
    its own.
    """

    first = clinic_factory(slug="rls-write-a")
    second = clinic_factory(slug="rls-write-b")

    with as_role(APP_ROLE), rls_context(first.id):
        with pytest.raises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO patients_patient "
                    "(id, clinic_id, number, first_name, last_name, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, now(), now())",
                    [
                        uuid.uuid4(),
                        str(second.id),
                        "X-1",
                        "Mallory",
                        "Cross",
                    ],
                )


def test_a_tenant_reads_no_platform_audit_row(clinic_factory):
    from apps.core.models import AuditEvent

    clinic = clinic_factory(slug="rls-audit")
    AuditEvent.objects.create(
        clinic_id=None,
        action="platform.probe",
        entity_type="",
        entity_id="",
        prev_hash="",
        hash="probe",
    )
    AuditEvent.objects.create(
        clinic_id=clinic.id,
        action="tenant.probe",
        entity_type="",
        entity_id="",
        prev_hash="",
        hash="probe",
    )

    with as_role(APP_ROLE), with_clinic(clinic):
        actions = set(AuditEvent.objects.values_list("action", flat=True))
        assert "tenant.probe" in actions
        assert "platform.probe" not in actions


def test_a_tenant_reads_no_platform_job_row(clinic_factory):
    from apps.core.models import JobRequest

    clinic = clinic_factory(slug="rls-jobs")
    JobRequest.objects.create(kind="platform.probe", clinic_id=None)
    JobRequest.objects.create(kind="tenant.probe", clinic_id=clinic.id)

    with as_role(APP_ROLE), with_clinic(clinic):
        kinds = set(JobRequest.objects.values_list("kind", flat=True))
        assert "tenant.probe" in kinds
        assert "platform.probe" not in kinds


def test_a_tenant_reads_the_unassigned_reconciliation_queue(
    clinic_factory
):
    """The deliberate cross-tenant read of ADR-013 section 2."""

    from apps.billing.models import ReconciliationItem, WebhookEvent

    clinic = clinic_factory(slug="rls-reconcile")
    event = WebhookEvent.objects.create(
        gateway="sandbox",
        event_id="evt_rls_reconcile",
        payload_json={},
        signature_valid=True,
        status=WebhookEvent.Status.UNMATCHED,
    )
    ReconciliationItem.objects.create(
        clinic_id=None,
        event=event,
        reason="unmatched_payment",
        amount_minor=100,
        status=ReconciliationItem.Status.OPEN,
    )
    ReconciliationItem.objects.create(
        clinic_id=clinic.id,
        event=event,
        reason="tenant",
        amount_minor=100,
        status=ReconciliationItem.Status.OPEN,
    )

    with as_role(APP_ROLE), with_clinic(clinic):
        reasons = set(ReconciliationItem.objects.values_list("reason", flat=True))
        assert "unmatched_payment" in reasons
        assert "tenant" in reasons


def test_a_principal_reads_its_own_membership_without_a_clinic_context(
    clinic_factory, user_factory, membership_factory
):
    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="rls-principal")
    user = user_factory("rls-principal@bright.test")
    membership_factory(clinic, user, role="owner")

    with as_role(APP_ROLE), rls_context(None, "", principal_id=user.id):
        rows = list(Membership.all_objects.filter(user=user))
        assert len(rows) == 1
        assert rows[0].clinic_id == clinic.id


def test_a_principal_reads_no_membership_of_another_principal(
    clinic_factory, user_factory, membership_factory
):
    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="rls-principal-other")
    user = user_factory("rls-principal-other@bright.test")
    other = user_factory("rls-principal-other-2@bright.test")
    membership_factory(clinic, user, role="owner")
    membership_factory(clinic, other, role="dentist")

    with as_role(APP_ROLE), rls_context(None, "", principal_id=user.id):
        users = set(Membership.all_objects.values_list("user_id", flat=True))
        assert users == {user.id}


def test_a_principal_cannot_update_its_own_membership_without_a_clinic_context(
    clinic_factory, user_factory, membership_factory
):
    from apps.clinics.models import Membership

    clinic = clinic_factory(slug="rls-principal-write")
    user = user_factory("rls-principal-write@bright.test")
    membership_factory(clinic, user, role="owner")

    with as_role(APP_ROLE), rls_context(None, "", principal_id=user.id):
        updated = (
            Membership.all_objects.filter(user=user)
            .update(role="dentist")
        )
        assert updated == 0


def test_a_portal_session_is_resolved_by_its_key_hash(clinic_factory):
    from apps.portal.models import PatientSession
    from apps.portal.services import identity as identity_service

    clinic = clinic_factory(slug="rls-session")
    patient = make_patient(clinic, "S-1", "Sol")
    session, raw_key = identity_service.create_session(patient)

    with as_role(APP_ROLE), rls_context(
        None, "", portal_key_hash=identity_service.hash_key(raw_key)
    ):
        rows = list(PatientSession.objects.filter(key_hash=identity_service.hash_key(raw_key)))
        assert [row.id for row in rows] == [session.id]


def test_a_portal_reads_no_session_of_another_patient_in_the_same_clinic(
    clinic_factory
):
    from apps.portal.models import PatientSession
    from apps.portal.services import identity as identity_service

    clinic = clinic_factory(slug="rls-session-other")
    first = make_patient(clinic, "S-2", "One")
    second = make_patient(clinic, "S-3", "Two")
    _session, first_key = identity_service.create_session(first)
    identity_service.create_session(second)

    with as_role(APP_ROLE), rls_context(
        None, "", portal_key_hash=identity_service.hash_key(first_key)
    ):
        rows = set(PatientSession.objects.values_list("patient_id", flat=True))
        assert rows == {first.id}


def test_the_app_role_cannot_bypass_rls_by_setting_the_guc(
    clinic_factory
):
    first = clinic_factory(slug="rls-bypass-app-a")
    second = clinic_factory(slug="rls-bypass-app-b")
    make_patient(first, "E-1", "Eve")
    make_patient(second, "F-1", "Finn")

    with as_role(APP_ROLE), bypass_rls():
        assert Patient.all_objects.count() == 0


def test_the_maintenance_role_can_bypass_rls_by_setting_the_guc(
    clinic_factory
):
    first = clinic_factory(slug="rls-bypass-maint-a")
    second = clinic_factory(slug="rls-bypass-maint-b")
    make_patient(first, "E-1", "Eve")
    make_patient(second, "F-1", "Finn")

    with as_role(MAINT_ROLE):
        assert Patient.all_objects.count() == 0
        with bypass_rls():
            assert Patient.all_objects.count() == 2


def test_a_signed_webhook_resolves_its_payment_with_no_clinic_context(
    clinic_factory
):
    from apps.billing.models import Invoice, Payment
    from apps.billing.services import reconcile

    clinic = clinic_factory(slug="rls-webhook")
    patient = make_patient(clinic, "W-1", "Wanda")
    invoice = Invoice.all_objects.create(
        clinic=clinic,
        patient=patient,
        number="INV-RLS-1",
        status="issued",
        currency="INR",
        subtotal_minor=1000,
        total_minor=1000,
        balance_minor=1000,
    )
    Payment.all_objects.create(
        clinic=clinic,
        invoice=invoice,
        amount_minor=1000,
        currency="INR",
        method=Payment.Method.CARD,
        status=Payment.Status.PENDING,
        gateway="sandbox",
        gateway_payment_id="pay_rls_1",
    )

    with as_role(APP_ROLE):
        result = reconcile.process_event(
            "sandbox",
            {
                "event_id": "evt_rls_1",
                "type": "payment.succeeded",
                "gateway_payment_id": "pay_rls_1",
                "amount_minor": 1000,
            },
        )

    assert result["status"] == "processed"


def test_a_signed_webhook_with_an_unknown_reference_is_unmatched(
    clinic_factory
):
    from apps.billing.services import reconcile

    clinic_factory(slug="rls-webhook-unknown")

    with as_role(APP_ROLE):
        result = reconcile.process_event(
            "sandbox",
            {
                "event_id": "evt_rls_2",
                "type": "payment.succeeded",
                "gateway_payment_id": "pay_does_not_exist",
                "amount_minor": 1000,
            },
        )

    assert result["status"] == "unmatched"


def test_a_request_without_a_context_reads_no_tenant_row(clinic_factory):
    first = clinic_factory(slug="rls-none-a")
    second = clinic_factory(slug="rls-none-b")
    make_patient(first, "C-1", "Cara")
    make_patient(second, "D-1", "Daan")

    with as_role(APP_ROLE):
        with pytest.raises(UnscopedQueryError):
            list(Patient.objects.all())
        assert Patient.all_objects.count() == 0


# --- guessed identifiers ----------------------------------------------------


def test_a_guessed_identifier_yields_nothing(clinic_factory):
    """A foreign patient id is not reachable, even by a direct primary key."""

    from apps.billing.models import Invoice
    from apps.scheduling.models import Appointment

    first = clinic_factory(slug="rls-guess-a")
    second = clinic_factory(slug="rls-guess-b")
    own = make_patient(first, "G-1", "Gia")
    foreign = make_patient(second, "H-1", "Hugo")
    invoice = Invoice.all_objects.create(
        clinic=second,
        patient=foreign,
        number="INV-RLS-2",
        status="issued",
        currency="INR",
        subtotal_minor=100,
        total_minor=100,
        balance_minor=100,
    )

    with as_role(APP_ROLE), with_clinic(first):
        assert Patient.all_objects.filter(pk=foreign.id).count() == 0
        assert Invoice.all_objects.filter(pk=invoice.id).count() == 0
        assert Appointment.all_objects.filter(clinic_id=second.id).count() == 0
        assert Patient.all_objects.filter(pk=own.id).count() == 1


def test_another_patients_session_cookie_yields_nothing(
    clinic_factory
):
    from apps.portal.models import PatientSession
    from apps.portal.services import identity as identity_service

    clinic = clinic_factory(slug="rls-guess-session")
    first = make_patient(clinic, "G-2", "Gil")
    second = make_patient(clinic, "G-3", "Gus")
    _session, second_key = identity_service.create_session(second)

    with as_role(APP_ROLE), rls_context(
        None, "", portal_key_hash=identity_service.hash_key("a-guessed-cookie")
    ):
        assert PatientSession.objects.count() == 0

    with as_role(APP_ROLE), rls_context(
        None, "", portal_key_hash=identity_service.hash_key(second_key)
    ):
        rows = set(PatientSession.objects.values_list("patient_id", flat=True))
        assert rows == {second.id}
        assert first.id not in rows


# --- privilege escalation ---------------------------------------------------


@contextmanager
def as_session_user(role: str):
    """Run the block with ``role`` as the *session* user.

    ``SET ROLE`` is checked against the session user, not against the current
    role, so the escalation proofs have to replace the session user as well.
    Only a superuser may do that, which is what the test connection is.
    """

    with connection.cursor() as cursor:
        cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
    try:
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
        except Exception:  # pragma: no cover - transaction already aborted
            pass


def test_the_app_role_cannot_switch_to_the_bypass_holder():
    with as_session_user(APP_ROLE):
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute(f'SET ROLE "{MAINT_ROLE}"')


def test_the_app_role_cannot_switch_to_the_owner():
    with as_session_user(APP_ROLE):
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute("SET ROLE enamel")


def test_the_app_role_cannot_alter_privileges():
    with as_session_user(APP_ROLE):
        # A grant without the grant option is a no-op: the grantee gains
        # nothing.
        with connection.cursor() as cursor:
            cursor.execute("GRANT SELECT ON clinics_membership TO enamel_portal")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT has_table_privilege('enamel_portal', 'clinics_membership', 'SELECT')"
            )
            assert cursor.fetchone()[0] is False
        # The remaining statements are refused outright.
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE patients_patient DISABLE ROW LEVEL SECURITY")
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute("CREATE ROLE enamel_evil LOGIN SUPERUSER")
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute("ALTER ROLE enamel_app BYPASSRLS")
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE POLICY probe ON patients_patient FOR SELECT TO enamel_app USING (true)"
                )


# --- the portal principal ---------------------------------------------------


def test_the_portal_role_reads_no_row_of_another_patient_in_the_same_clinic(
    clinic_factory
):
    clinic = clinic_factory(slug="rls-portal-a")
    first = make_patient(clinic, "P-1", "Pam")
    second = make_patient(clinic, "P-2", "Pip")

    with as_role(PORTAL_ROLE), rls_context(
        clinic.id, "", principal_id=None, portal_patient_id=first.id
    ):
        assert Patient.all_objects.count() == 1
        assert Patient.all_objects.filter(pk=second.id).count() == 0


def test_the_portal_role_cannot_read_the_staff_only_tables(
    clinic_factory, user_factory, membership_factory
):
    """Memberships, the staff audit chain and the internal notes are closed."""

    from apps.clinics.models import Membership
    from apps.core.models import AuditEvent

    clinic = clinic_factory(slug="rls-portal-staff")
    user = user_factory("rls-portal-staff@bright.test")
    membership_factory(clinic, user, role="owner")
    patient = make_patient(clinic, "P-3", "Poe")
    AuditEvent.objects.create(
        clinic_id=clinic.id,
        action="staff.probe",
        entity_type="",
        entity_id="",
        prev_hash="",
        hash="probe",
    )

    with as_role(PORTAL_ROLE), rls_context(clinic.id, "", portal_patient_id=patient.id):
        with pytest.raises(DatabaseError):
            list(Membership.all_objects.all())
        with pytest.raises(DatabaseError):
            list(AuditEvent.objects.all())
        # The internal notes of the patient are not readable either: the
        # portal role has no grant on the notes column.
        with pytest.raises(DatabaseError):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT notes FROM patients_patient WHERE id = %s", [patient.id]
                )


def test_the_portal_auth_role_cannot_read_a_clinical_row(
    clinic_factory
):
    from apps.billing.models import Invoice
    from apps.scheduling.models import Appointment

    clinic = clinic_factory(slug="rls-portal-auth")
    patient = make_patient(clinic, "P-4", "Pol")
    Invoice.all_objects.create(
        clinic=clinic,
        patient=patient,
        number="INV-RLS-3",
        status="issued",
        currency="INR",
        subtotal_minor=100,
        total_minor=100,
        balance_minor=100,
    )

    with as_role(PORTAL_AUTH_ROLE), rls_context(
        clinic.id, "", portal_contact="+919000000000"
    ):
        assert Patient.all_objects.count() == 0
        with pytest.raises(DatabaseError):
            list(Invoice.all_objects.all())
        with pytest.raises(DatabaseError):
            list(Appointment.all_objects.all())


def test_the_portal_auth_role_may_resolve_the_patient_of_a_presented_contact(
    clinic_factory
):
    clinic = clinic_factory(slug="rls-portal-auth-resolve")
    patient = Patient.all_objects.create(
        clinic=clinic,
        number="P-5",
        first_name="Pim",
        last_name="Resolve",
        phone_e164="+919876500099",
    )

    with as_role(PORTAL_AUTH_ROLE), rls_context(
        clinic.id, "", portal_contact="+919876500099"
    ):
        rows = list(Patient.all_objects.all())
        assert [row.id for row in rows] == [patient.id]


# --- context hygiene --------------------------------------------------------


def test_the_job_context_does_not_leak_into_the_next_task(clinic_factory):
    from apps.core.jobs import durable_task, enqueue, run_job

    first = clinic_factory(slug="rls-job-a")
    second = clinic_factory(slug="rls-job-b")
    make_patient(first, "J-1", "Jia")
    make_patient(second, "J-2", "Jin")

    seen: dict[str, object] = {}

    @durable_task("test.rls.leak")
    def handler(payload: dict) -> dict:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('app.current_clinic_id', true), "
                "current_setting('app.current_portal_patient_id', true), "
                "current_setting('app.current_principal_id', true)"
            )
            seen["gucs"] = cursor.fetchone()
        seen["visible"] = Patient.all_objects.count()
        return {"ok": True}

    job = enqueue("test.rls.leak", {}, clinic=first)

    # The worker is the role that runs the jobs: it is a member of the
    # platform group, so it sees the tenant and the platform job rows.
    with as_role(WORKER_ROLE):
        run_job(job.id)
        # The task left no context behind.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('app.current_clinic_id', true), "
                "current_setting('app.current_portal_patient_id', true), "
                "current_setting('app.current_principal_id', true)"
            )
            row = cursor.fetchone()
        assert row[0] in (None, "")
        assert row[1] in (None, "")
        assert row[2] in (None, "")
        # And the role is the one the task started with.
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            assert cursor.fetchone()[0] == WORKER_ROLE

    job.refresh_from_db()
    assert job.status == "done"
    assert seen["visible"] == 1
    assert seen["gucs"][0] == str(first.id)


# --- unauthorised administrative access -------------------------------------


def test_a_staff_user_cannot_reach_the_platform_admin_surface(
    api_client, user_factory, clinic_factory, membership_factory
):
    """Decision D-1: the admin is a separate service, not a tenant surface."""

    clinic = clinic_factory(slug="rls-admin-staff")
    user = user_factory("rls-admin-staff@bright.test")
    membership_factory(clinic, user, role="owner")
    api_client.force_login(user)

    response = api_client.get("/admin/")
    assert response.status_code in (302, 403)


def test_a_portal_user_cannot_reach_a_staff_endpoint(
    api_client, clinic_factory
):
    from apps.portal.services import identity as identity_service

    clinic = clinic_factory(slug="rls-admin-portal")
    patient = make_patient(clinic, "P-6", "Pip")
    _session, raw_key = identity_service.create_session(patient)
    api_client.cookies["enamel_portal"] = raw_key

    response = api_client.get("/api/v1/patients/")
    assert response.status_code in (401, 403)


def test_a_portal_user_cannot_reach_the_platform_admin_surface(
    api_client, clinic_factory
):
    from apps.portal.services import identity as identity_service

    clinic = clinic_factory(slug="rls-admin-portal-admin")
    patient = make_patient(clinic, "P-7", "Pit")
    _session, raw_key = identity_service.create_session(patient)
    api_client.cookies["enamel_portal"] = raw_key

    response = api_client.get("/admin/")
    assert response.status_code in (302, 403)
