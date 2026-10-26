"""Principal-scoped row-level security (T137, ADR-013).

This migration replaces the single ``clinic_isolation`` predicate of
``0005_rls.py`` with the **role-scoped** policy inventory of ADR-013 and
creates the database roles the inventory is written against.

Why role-scoped
---------------

``0005_rls.py`` created every policy ``TO PUBLIC``. ``TO PUBLIC`` applies to
*every* role, so a patient-portal role would still have been governed by the
staff policies - and a staff role would have been governed by the portal
policies. The inventory below therefore names the role every policy is
written for:

===========================  ==========================================
Role                         Purpose
===========================  ==========================================
``enamel`` (table owner)     the compose owner; holds ``clinic_bypass``
``enamel_app``               the staff/API runtime
``enamel_worker``            the worker/beat runtime (member of
                             ``enamel_app`` and ``enamel_platform``)
``enamel_platform``          the cross-clinic job access of the worker
``enamel_portal``            the patient-scoped policies of the portal
``enamel_portal_auth``       the minimal one-time-code bootstrap role
``enamel_maint``             the maintenance role; the only non-owner role
                             that holds the bypass
===========================  ==========================================

The five classes of ADR-013 section 2 map onto the inventory below:

* **T - tenant**: ``clinic_isolation`` (``TO enamel_app``).
* **P - principal**: the principal key of the requesting user, the portal
  session, the one-time-code challenge and the idempotency record.
* **C - capability**: the invitation token, the signed payment webhook.
* **G - global catalogue**: the shared no-show model.
* **X - platform-private**: insert-only platform rows that no tenant may read.

The two portal-owned tables (``portal_patientsession`` and
``portal_portalotpchallenge``) do **not** receive ``clinic_isolation``: their
only policies are the principal ones, so a portal request can never read
another patient's session row (which carries the session ``key_hash``).

The reverse function restores the ``0005`` policy set exactly - including the
``TO PUBLIC`` policies of that migration - so the rollback is a real inverse
and not a no-op. The roles are cluster-wide objects and are deliberately left
in place: an unused role is harmless, and dropping it would need the grants
revoked first.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import migrations

# --- roles ------------------------------------------------------------------

ROLE_APP = "enamel_app"
ROLE_WORKER = "enamel_worker"
ROLE_PLATFORM = "enamel_platform"
ROLE_PORTAL = "enamel_portal"
ROLE_PORTAL_AUTH = "enamel_portal_auth"
ROLE_MAINT = "enamel_maint"

#: Every role this migration creates, with the login attribute it gets.
LOGIN_ROLES: tuple[str, ...] = (ROLE_APP, ROLE_WORKER, ROLE_MAINT)
NOLOGIN_ROLES: tuple[str, ...] = (ROLE_PLATFORM, ROLE_PORTAL, ROLE_PORTAL_AUTH)

#: The tables the portal role may read. Everything else is out of its reach -
#: the membership table, the staff audit chain and the clinical notes are not
#: in the list, and the role has no grant on them at all.
PORTAL_TABLES: tuple[str, ...] = (
    "booking_bookingpolicy",
    "billing_invoice",
    "billing_payment",
    "clinics_clinic",
    "clinics_location",
    "clinics_provider",
    "core_auditevent",
    "patients_allergy",
    "patients_encounter",
    "patients_medicalhistory",
    "patients_patient",
    "plans_document",
    "plans_planitem",
    "plans_treatmentplan",
    "portal_patientsession",
    "portal_portalotpchallenge",
    "scheduling_appointment",
    "scheduling_appointmentevent",
    "scheduling_appointmenttype",
)

#: The tables the one-time-code bootstrap role may touch: the clinic
#: catalogue, the patient index of that clinic and the challenge rows.
PORTAL_AUTH_TABLES: tuple[str, ...] = (
    "clinics_clinic",
    "core_auditevent",
    "patients_patient",
    "portal_patientsession",
    "portal_portalotpchallenge",
)

# --- context expressions ----------------------------------------------------

CLINIC = "nullif(current_setting('app.current_clinic_id', true), '')::uuid"
PRINCIPAL = "nullif(current_setting('app.current_principal_id', true), '')::uuid"
PATIENT = "nullif(current_setting('app.current_portal_patient_id', true), '')::uuid"
CHALLENGE = "nullif(current_setting('app.current_portal_challenge_id', true), '')::uuid"
KEY_HASH = "nullif(current_setting('app.current_portal_key_hash', true), '')"
INVITATION_HASH = "nullif(current_setting('app.current_invitation_token_hash', true), '')"
CONTACT = "nullif(current_setting('app.current_portal_contact', true), '')"
WEBHOOK_GATEWAY = "nullif(current_setting('app.current_webhook_gateway', true), '')"
WEBHOOK_EVENT = "nullif(current_setting('app.current_webhook_event_id', true), '')"
WEBHOOK_REFERENCE = "nullif(current_setting('app.current_webhook_reference', true), '')"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: The patient rows of the portal principal.
PORTAL_PATIENT_ROW = f"id = {PATIENT} AND clinic_id = {CLINIC}"
#: The patient rows of the patient-linked tenant tables.
PORTAL_PATIENT_LINK = f"patient_id = {PATIENT} AND clinic_id = {CLINIC}"
#: The plan items of the patient's own plan.
PORTAL_PLAN_ITEM = (
    f"clinic_id = {CLINIC} AND plan_id IN ("
    f"SELECT id FROM plans_treatmentplan WHERE patient_id = {PATIENT} AND clinic_id = {CLINIC}"
    f")"
)
#: The appointments of the patient (used by the appointment-event policies).
PORTAL_APPOINTMENT = (
    f"SELECT id FROM scheduling_appointment WHERE patient_id = {PATIENT} AND clinic_id = {CLINIC}"
)
#: The invoices of the patient (used by the payment policy).
PORTAL_INVOICE = (
    f"SELECT id FROM billing_invoice WHERE patient_id = {PATIENT} AND clinic_id = {CLINIC}"
)
#: The patient row the one-time-code bootstrap may resolve: the row of the
#: presented contact inside the named clinic, or the row of the resolved
#: patient id (the verify step).
PORTAL_AUTH_PATIENT = (
    f"clinic_id = {CLINIC} AND (id = {PATIENT} "
    f"OR lower(email) = lower({CONTACT}) OR phone_e164 = {CONTACT})"
)


@dataclass(frozen=True)
class PolicySpec:
    """One policy of the inventory."""

    name: str
    command: str
    roles: tuple[str, ...]
    using: str = ""
    check: str = ""
    #: The table owner is added to the policy when the policy is meant to be
    #: the owner's own escape hatch (the maintenance bypass). Every other
    #: policy is written for the named roles only.
    include_owner: bool = False

    def sql(self, table: str, owner: str) -> str:
        role_names = list(self.roles)
        if self.include_owner and owner:
            role_names.append(owner)
        roles = ", ".join(f'"{role}"' for role in role_names)
        statement = (
            f'CREATE POLICY "{self.name}" ON "{table}" FOR {self.command} TO {roles}'
        )
        if self.using:
            statement += f" USING ({self.using})"
        if self.check:
            statement += f" WITH CHECK ({self.check})"
        return statement


@dataclass(frozen=True)
class TablePolicy:
    """The policy set of one table."""

    table: str
    policies: tuple[PolicySpec, ...] = field(default_factory=tuple)


def _bypass() -> PolicySpec:
    """The maintenance bypass: the owner and the maintenance role only.

    The policy is never ``TO PUBLIC``, so setting ``app.bypass_rls`` is not an
    escape hatch for the application role (ADR-013 section 6, decision D-2).
    """

    return PolicySpec(
        name="clinic_bypass",
        command="ALL",
        roles=(ROLE_MAINT,),
        using=BYPASS,
        check=BYPASS,
        include_owner=True,
    )


def _class_t() -> tuple[PolicySpec, ...]:
    return (
        PolicySpec(
            name="clinic_isolation",
            command="ALL",
            roles=(ROLE_APP,),
            using=f"clinic_id = {CLINIC}",
            check=f"clinic_id = {CLINIC}",
        ),
    )


def _class_p() -> dict[str, tuple[PolicySpec, ...]]:
    """The principal policies (the row is addressed by a principal key)."""

    return {
        "clinics_membership": (
            PolicySpec(
                name="principal_membership_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using=f"user_id = {PRINCIPAL}",
            ),
        ),
        "core_idempotencyrecord": (
            PolicySpec(
                name="principal_idempotency",
                command="ALL",
                roles=(ROLE_APP,),
                using=f"user_id = {PRINCIPAL}",
                check=f"user_id = {PRINCIPAL}",
            ),
        ),
        "portal_patientsession": (
            PolicySpec(
                name="principal_session_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"key_hash = {KEY_HASH}",
            ),
            PolicySpec(
                name="principal_session_update",
                command="UPDATE",
                roles=(ROLE_PORTAL,),
                using=f"key_hash = {KEY_HASH}",
                check=f"key_hash = {KEY_HASH}",
            ),
            PolicySpec(
                name="principal_session_insert",
                command="INSERT",
                roles=(ROLE_PORTAL, ROLE_PORTAL_AUTH),
                check=f"patient_id = {PATIENT} AND clinic_id = {CLINIC}",
            ),
        ),
        "portal_portalotpchallenge": (
            PolicySpec(
                name="principal_challenge_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"id = {CHALLENGE}",
            ),
            PolicySpec(
                name="principal_challenge_update",
                command="UPDATE",
                roles=(ROLE_PORTAL,),
                using=f"id = {CHALLENGE}",
                check=f"id = {CHALLENGE} AND clinic_id = {CLINIC} AND patient_id = {PATIENT}",
            ),
            PolicySpec(
                name="principal_challenge_insert",
                command="INSERT",
                roles=(ROLE_PORTAL, ROLE_PORTAL_AUTH),
                check=f"patient_id = {PATIENT} AND clinic_id = {CLINIC}",
            ),
        ),
    }


def _class_c() -> dict[str, tuple[PolicySpec, ...]]:
    """The capability policies (the row is addressed by a verified token)."""

    return {
        "clinics_invitation": (
            PolicySpec(
                name="capability_invitation_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using=f"token_hash = {INVITATION_HASH}",
            ),
        ),
        "billing_payment": (
            PolicySpec(
                name="capability_payment_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using=f"gateway = {WEBHOOK_GATEWAY} AND gateway_payment_id = {WEBHOOK_REFERENCE}",
            ),
        ),
        "billing_webhookevent": (
            PolicySpec(
                name="capability_webhook_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using=f"gateway = {WEBHOOK_GATEWAY} AND event_id = {WEBHOOK_EVENT}",
            ),
            PolicySpec(
                name="capability_webhook_update",
                command="UPDATE",
                roles=(ROLE_APP,),
                using=f"gateway = {WEBHOOK_GATEWAY} AND event_id = {WEBHOOK_EVENT}",
                check=f"clinic_id IS NULL OR clinic_id = {CLINIC}",
            ),
        ),
    }


def _class_g() -> dict[str, tuple[PolicySpec, ...]]:
    """The global-catalogue policies (a platform row that is shared)."""

    return {
        "ml_noshowmodel": (
            PolicySpec(
                name="global_catalogue_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using=f"clinic_id IS NULL OR clinic_id = {CLINIC}",
            ),
        ),
    }


def _class_x() -> dict[str, tuple[PolicySpec, ...]]:
    """The platform-private policies (write-only for the application role)."""

    return {
        "core_auditevent": (
            PolicySpec(
                name="platform_audit_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
        ),
        "core_jobrequest": (
            PolicySpec(
                name="platform_job_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
            # The scheduler is cross-clinic by design: the worker may see every
            # job row, tenant and platform alike.
            PolicySpec(
                name="platform_job_access",
                command="ALL",
                roles=(ROLE_PLATFORM,),
                using="true",
                check="true",
            ),
        ),
        "ml_trainingrun": (
            PolicySpec(
                name="platform_training_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
        ),
        "ai_aiinvocation": (
            PolicySpec(
                name="platform_invocation_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
        ),
    }


def _shared_platform() -> dict[str, tuple[PolicySpec, ...]]:
    """The deliberately shared platform rows (the unassigned queue)."""

    return {
        "billing_reconciliationitem": (
            PolicySpec(
                name="platform_shared_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using="clinic_id IS NULL",
            ),
            PolicySpec(
                name="platform_reconciliation_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
        ),
        "billing_webhookevent": (
            PolicySpec(
                name="platform_shared_read",
                command="SELECT",
                roles=(ROLE_APP,),
                using="clinic_id IS NULL",
            ),
            PolicySpec(
                name="platform_webhook_insert",
                command="INSERT",
                roles=(ROLE_APP,),
                check="clinic_id IS NULL",
            ),
        ),
    }


def _portal_policies() -> dict[str, tuple[PolicySpec, ...]]:
    """The patient-scoped policies of the portal principal."""

    return {
        "patients_patient": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_ROW,
            ),
        ),
        "patients_allergy": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "patients_medicalhistory": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "patients_encounter": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        # The reminder sequence reads the consent record of the patient to
        # pick the channel. Only the patient's own row is reachable.
        "messaging_consent": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "scheduling_appointment": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
            PolicySpec(
                name="portal_patient_update",
                command="UPDATE",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
                check=PORTAL_PATIENT_LINK,
            ),
            PolicySpec(
                name="portal_patient_insert",
                command="INSERT",
                roles=(ROLE_PORTAL,),
                check=PORTAL_PATIENT_LINK,
            ),
        ),
        "scheduling_appointmentevent": (
            PolicySpec(
                name="portal_appointment_event_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"appointment_id IN ({PORTAL_APPOINTMENT})",
            ),
            PolicySpec(
                name="portal_appointment_event_insert",
                command="INSERT",
                roles=(ROLE_PORTAL,),
                check=f"appointment_id IN ({PORTAL_APPOINTMENT})",
            ),
        ),
        "scheduling_appointmenttype": (
            PolicySpec(
                name="portal_clinic_reference_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
        ),
        "clinics_provider": (
            PolicySpec(
                name="portal_clinic_reference_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
            # The reschedule path locks the provider row (``SELECT ... FOR
            # UPDATE``), which PostgreSQL only allows with the UPDATE
            # privilege. The WITH CHECK denies every modification, so the lock
            # is possible and the row is not.
            PolicySpec(
                name="portal_clinic_reference_lock",
                command="UPDATE",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
                check="false",
            ),
        ),
        "clinics_location": (
            PolicySpec(
                name="portal_clinic_reference_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
        ),
        "booking_bookingpolicy": (
            PolicySpec(
                name="portal_clinic_reference_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
            PolicySpec(
                name="portal_clinic_reference_insert",
                command="INSERT",
                roles=(ROLE_PORTAL,),
                check=f"clinic_id = {CLINIC}",
            ),
        ),
        "plans_treatmentplan": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "plans_planitem": (
            PolicySpec(
                name="portal_plan_item_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PLAN_ITEM,
            ),
            PolicySpec(
                name="portal_plan_item_update",
                command="UPDATE",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PLAN_ITEM,
                check=PORTAL_PLAN_ITEM,
            ),
        ),
        "billing_invoice": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "billing_payment": (
            PolicySpec(
                name="portal_payment_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC} AND invoice_id IN ({PORTAL_INVOICE})",
            ),
        ),
        "plans_document": (
            PolicySpec(
                name="portal_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=PORTAL_PATIENT_LINK,
            ),
        ),
        "core_auditevent": (
            PolicySpec(
                name="portal_audit_insert",
                command="INSERT",
                roles=(ROLE_PORTAL, ROLE_PORTAL_AUTH),
                check=f"clinic_id = {CLINIC}",
            ),
        ),
        "core_jobrequest": (
            PolicySpec(
                name="portal_job_read",
                command="SELECT",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
            PolicySpec(
                name="portal_job_insert",
                command="INSERT",
                roles=(ROLE_PORTAL,),
                check=f"clinic_id = {CLINIC}",
            ),
            PolicySpec(
                name="portal_job_delete",
                command="DELETE",
                roles=(ROLE_PORTAL,),
                using=f"clinic_id = {CLINIC}",
            ),
        ),
    }


def _portal_auth_policies() -> dict[str, tuple[PolicySpec, ...]]:
    """The policies of the minimal one-time-code bootstrap role."""

    return {
        "patients_patient": (
            PolicySpec(
                name="portal_auth_patient_read",
                command="SELECT",
                roles=(ROLE_PORTAL_AUTH,),
                using=PORTAL_AUTH_PATIENT,
            ),
        ),
        "portal_portalotpchallenge": (
            PolicySpec(
                name="portal_auth_challenge_read",
                command="SELECT",
                roles=(ROLE_PORTAL_AUTH,),
                using=f"id = {CHALLENGE}",
            ),
            PolicySpec(
                name="portal_auth_challenge_update",
                command="UPDATE",
                roles=(ROLE_PORTAL_AUTH,),
                using=f"id = {CHALLENGE}",
                check=f"id = {CHALLENGE} AND clinic_id = {CLINIC} AND patient_id = {PATIENT}",
            ),
            PolicySpec(
                name="portal_auth_challenge_insert",
                command="INSERT",
                roles=(ROLE_PORTAL_AUTH,),
                check=f"patient_id = {PATIENT} AND clinic_id = {CLINIC}",
            ),
        ),
    }


#: The tables that never receive ``clinic_isolation``: the two portal-owned
#: tables are read and written by principal only.
PORTAL_OWNED_TABLES: frozenset[str] = frozenset(
    {"portal_patientsession", "portal_portalotpchallenge"}
)


def _tenant_tables(connection) -> list[tuple[str, bool, str]]:
    """Return ``(table, clinic_id_is_nullable, owner)`` for every tenant table."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, NOT a.attnotnull AS nullable,
                   pg_get_userbyid(c.relowner) AS owner
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
        return [(row[0], bool(row[1]), str(row[2])) for row in cursor.fetchall()]


def _quote(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


ROLE_DDL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_app') THEN
        CREATE ROLE enamel_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_worker') THEN
        CREATE ROLE enamel_worker LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_platform') THEN
        CREATE ROLE enamel_platform NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_portal') THEN
        CREATE ROLE enamel_portal NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_portal_auth') THEN
        CREATE ROLE enamel_portal_auth NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_maint') THEN
        CREATE ROLE enamel_maint LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

-- The attributes are re-asserted on every run so an existing role can never
-- drift back to a superuser or to BYPASSRLS.
ALTER ROLE enamel_app LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE enamel_worker LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE enamel_platform NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE enamel_portal NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE enamel_portal_auth NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE enamel_maint LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- Memberships. The application role may narrow itself to the patient-scoped
-- portal role (it may never widen itself: it is not a member of the
-- maintenance role and holds no bypass of any kind). The worker inherits the
-- staff policies and the cross-clinic job access of the platform group.
GRANT enamel_app TO enamel_worker;
GRANT enamel_platform TO enamel_worker;
GRANT enamel_portal TO enamel_app;
GRANT enamel_portal_auth TO enamel_app;
"""

GRANT_DDL = """
GRANT USAGE ON SCHEMA public TO enamel_app;
GRANT USAGE ON SCHEMA public TO enamel_worker;
GRANT USAGE ON SCHEMA public TO enamel_platform;
GRANT USAGE ON SCHEMA public TO enamel_portal;
GRANT USAGE ON SCHEMA public TO enamel_portal_auth;
GRANT USAGE ON SCHEMA public TO enamel_maint;

-- The staff runtime keeps the full data privileges of the shipped stack; the
-- row-level policies are what narrow it.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_app;

-- The worker inherits them from enamel_app.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_worker;

-- The platform group only needs the job table.
GRANT SELECT, INSERT, UPDATE, DELETE ON core_jobrequest TO enamel_platform;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_platform;
-- The maintenance role runs the platform commands (seeding, retention, audit
-- verification). Its power comes from the bypass policy, not from the grants.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_maint;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_maint;

-- Tables created *after* this migration runs are granted as well.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO enamel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO enamel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO enamel_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO enamel_worker;
"""

#: The grants of the two patient roles. They are deliberately narrow: the
#: portal role reaches the patient rows of the portal principal and the
#: reference rows of its own clinic, the bootstrap role reaches the clinic,
#: the patient index and the challenge rows - and nothing else.
PORTAL_GRANT_DDL = """
GRANT SELECT ON clinics_clinic TO enamel_portal;
GRANT SELECT ON scheduling_appointmenttype TO enamel_portal;
GRANT SELECT, UPDATE ON clinics_provider TO enamel_portal;
GRANT SELECT ON clinics_location TO enamel_portal;
GRANT SELECT, INSERT ON booking_bookingpolicy TO enamel_portal;
GRANT SELECT ON patients_patient TO enamel_portal;
GRANT SELECT ON patients_allergy TO enamel_portal;
GRANT SELECT ON patients_medicalhistory TO enamel_portal;
GRANT SELECT ON patients_encounter TO enamel_portal;
GRANT SELECT ON messaging_consent TO enamel_portal;
GRANT SELECT, INSERT, UPDATE ON scheduling_appointment TO enamel_portal;
GRANT SELECT, INSERT ON scheduling_appointmentevent TO enamel_portal;
GRANT SELECT ON plans_treatmentplan TO enamel_portal;
GRANT SELECT, UPDATE ON plans_planitem TO enamel_portal;
GRANT SELECT ON billing_invoice TO enamel_portal;
GRANT SELECT ON billing_payment TO enamel_portal;
GRANT SELECT ON plans_document TO enamel_portal;
GRANT INSERT ON core_auditevent TO enamel_portal;
-- The portal enqueues its own clinic's jobs (a cancelled appointment raises
-- a reminder job). The job payload holds ids and enum values only - the
-- enqueue path rejects PII and secrets outright - and the platform jobs
-- (clinic_id IS NULL) stay invisible.
GRANT SELECT, INSERT, DELETE ON core_jobrequest TO enamel_portal;
GRANT SELECT, UPDATE, INSERT ON portal_patientsession TO enamel_portal;GRANT SELECT, INSERT, UPDATE ON portal_portalotpchallenge TO enamel_portal;

GRANT SELECT ON clinics_clinic TO enamel_portal_auth;
GRANT SELECT ON patients_patient TO enamel_portal_auth;
GRANT SELECT, INSERT, UPDATE ON portal_portalotpchallenge TO enamel_portal_auth;
GRANT INSERT ON portal_patientsession TO enamel_portal_auth;
GRANT INSERT ON core_auditevent TO enamel_portal_auth;

-- The audit chain is appended to, so the id sequence of the audit table is
-- needed. Nothing else is: every other table the two patient roles touch has
-- a UUID primary key.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE c.relkind = 'S'
           AND n.nspname = current_schema()
           AND c.relname = 'core_auditevent_id_seq'
    ) THEN
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE core_auditevent_id_seq TO enamel_portal';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE core_auditevent_id_seq TO enamel_portal_auth';
    END IF;
END
$$;
"""

AUDIT_HELPER_SQL = """
CREATE OR REPLACE FUNCTION enamel_audit_prev_hash(p_clinic_id uuid)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_previous text;
    v_bypass text;
BEGIN
    v_bypass := current_setting('app.bypass_rls', true);
    PERFORM set_config('app.bypass_rls', 'on', true);
    SELECT ae.hash INTO v_previous
      FROM public.core_auditevent ae
     WHERE ae.clinic_id IS NOT DISTINCT FROM p_clinic_id
     ORDER BY ae.id DESC
     LIMIT 1;
    PERFORM set_config('app.bypass_rls', COALESCE(v_bypass, ''), true);
    RETURN COALESCE(v_previous, '');
END;
$$;

REVOKE ALL ON FUNCTION enamel_audit_prev_hash(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enamel_audit_prev_hash(uuid)
    TO enamel_app, enamel_worker, enamel_portal, enamel_portal_auth, enamel_maint;
"""

AUDIT_HELPER_REVERSE_SQL = """
DROP FUNCTION IF EXISTS enamel_audit_prev_hash(uuid);
"""


def _old_policies() -> tuple[str, ...]:
    return ("clinic_isolation", "clinic_bypass", "clinic_platform_scope")


def _new_policy_names() -> tuple[str, ...]:
    names: set[str] = set()
    inventories = (
        _class_p(),
        _class_c(),
        _class_g(),
        _class_x(),
        _shared_platform(),
        _portal_policies(),
        _portal_auth_policies(),
    )
    for inventory in inventories:
        for table_policies in inventory.values():
            for policy in table_policies:
                names.add(policy.name)
    names.update(spec.name for spec in _class_t())
    names.add("clinic_bypass")
    return tuple(sorted(names))

def _table_policies(table: str) -> tuple[PolicySpec, ...]:
    """The full policy set of one tenant table."""

    policies: list[PolicySpec] = [_bypass()]
    if table not in PORTAL_OWNED_TABLES:
        policies.extend(_class_t())
    policies.extend(_class_p().get(table, ()))
    policies.extend(_class_c().get(table, ()))
    policies.extend(_class_g().get(table, ()))
    policies.extend(_class_x().get(table, ()))
    policies.extend(_shared_platform().get(table, ()))
    policies.extend(_portal_policies().get(table, ()))
    policies.extend(_portal_auth_policies().get(table, ()))
    return tuple(policies)


def _create_roles(cursor) -> None:
    cursor.execute(ROLE_DDL)
    cursor.execute(GRANT_DDL)
    cursor.execute(PORTAL_GRANT_DDL)


def apply(apps, schema_editor) -> None:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.bypass_rls', 'on', true)")

    with connection.cursor() as cursor:
        _create_roles(cursor)

    # The policies are created for the owner of the table (the compose owner)
    # plus the named roles. The owner is resolved from the catalog so the name
    # is never hard-coded.
    for table, _nullable, owner in _tenant_tables(connection):
        with connection.cursor() as cursor:
            for policy in _old_policies():
                cursor.execute(f"DROP POLICY IF EXISTS {_quote(policy)} ON {_quote(table)}")
            for policy in _table_policies(table):
                cursor.execute(policy.sql(table, owner))

    with connection.cursor() as cursor:
        cursor.execute(AUDIT_HELPER_SQL)


def revert(apps, schema_editor) -> None:
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.bypass_rls', 'on', true)")

    with connection.cursor() as cursor:
        cursor.execute(AUDIT_HELPER_REVERSE_SQL)

    # Restore the 0005 policy set exactly: clinic_isolation and clinic_bypass
    # TO PUBLIC on every tenant table, plus clinic_platform_scope on every
    # table whose clinic_id is nullable.
    for table, nullable, _owner in _tenant_tables(connection):
        with connection.cursor() as cursor:
            for policy in _new_policy_names():
                cursor.execute(f"DROP POLICY IF EXISTS {_quote(policy)} ON {_quote(table)}")
            cursor.execute(
                f"CREATE POLICY clinic_isolation ON {_quote(table)} FOR ALL TO PUBLIC "
                f"USING (clinic_id = {CLINIC}) WITH CHECK (clinic_id = {CLINIC})"
            )
            cursor.execute(
                f"CREATE POLICY clinic_bypass ON {_quote(table)} FOR ALL TO PUBLIC "
                f"USING ({BYPASS}) WITH CHECK ({BYPASS})"
            )
            if nullable:
                cursor.execute(
                    f"CREATE POLICY clinic_platform_scope ON {_quote(table)} FOR ALL TO PUBLIC "
                    f"USING (clinic_id IS NULL AND {CLINIC} IS NULL) "
                    f"WITH CHECK (clinic_id IS NULL AND {CLINIC} IS NULL)"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_rls"),
    ]

    operations = [
        migrations.RunPython(apply, reverse_code=revert),
    ]
