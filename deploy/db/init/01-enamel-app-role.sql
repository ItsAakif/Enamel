-- Enamel database roles (ADR-013, principal-scoped row-level security).
--
-- The shipped compose stack runs the API as `enamel_app`, the Celery worker and
-- beat as `enamel_worker` and the platform commands as `enamel_maint`. None of
-- them is a superuser, so PostgreSQL's row-level security is IN FORCE for them
-- (the owner credential reaches the `migrate` service only). This script
-- creates those roles:
--
--   enamel_app          LOGIN, the staff/API runtime. NOSUPERUSER, NOBYPASSRLS,
--                       NOCREATEDB, NOCREATEROLE. It is a member of the two
--                       patient roles, so it can narrow itself to them for a
--                       portal request - and of nothing else.
--   enamel_worker       LOGIN, the worker/beat runtime. It inherits the staff
--                       policies from enamel_app and the cross-clinic job
--                       access from enamel_platform.
--   enamel_platform     NOLOGIN group, the cross-clinic job access of the
--                       worker (the scheduler must see tenant and platform
--                       jobs).
--   enamel_portal       NOLOGIN group, the patient-scoped policies of the
--                       patient portal.
--   enamel_portal_auth  NOLOGIN group, the minimal one-time-code bootstrap
--                       role: it may read the clinic, resolve the patient
--                       behind a presented contact and read or write the
--                       challenge rows - and nothing else.
--   enamel_maint        LOGIN, the platform maintenance role (audit_verify,
--                       apply_patient_retention, seed_demo, seed_rcm_fixtures).
--                       It is the only non-owner role that holds the
--                       maintenance bypass, and it is never a member of
--                       enamel_app.
--   enamel_admin        LOGIN, the platform-admin service (ADR-013, decision
--                       D-1). It is a member of the owner, so it reaches
--                       owner-level access with its own credential, on its own
--                       port. It is never a member of enamel_app and the
--                       clinic-facing API can never reach it.
--
-- The script is idempotent: it can be run against a fresh volume (it is
-- mounted into /docker-entrypoint-initdb.d/) and re-run against a running
-- database (deploy/apply-app-role.ps1) without touching any data.
--
-- The row-level-security policies themselves are installed by the migration
-- `core/migrations/0006_principal_scoped_rls.py`, which is also where the
-- table privileges below are granted (the tables exist by then). The grants
-- repeated here make the script self-sufficient for a database that is
-- already migrated.

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
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'enamel_admin') THEN
        CREATE ROLE enamel_admin LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
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
ALTER ROLE enamel_admin LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- Memberships. The application role may narrow itself to the patient-scoped
-- roles (it may never widen itself: it is not a member of the maintenance
-- role and holds no bypass of any kind).
GRANT enamel_app TO enamel_worker;
GRANT enamel_platform TO enamel_worker;
GRANT enamel_portal TO enamel_app;
GRANT enamel_portal_auth TO enamel_app;

-- The platform-admin service is a member of the owner (the role that runs
-- this script), so its own credential reaches owner-level access. No runtime
-- role is a member of the owner.
DO $$
BEGIN
    EXECUTE format('GRANT %I TO enamel_admin', current_user);
END
$$;

-- Schema privileges.
GRANT USAGE ON SCHEMA public TO enamel_app;
GRANT USAGE ON SCHEMA public TO enamel_worker;
GRANT USAGE ON SCHEMA public TO enamel_platform;
GRANT USAGE ON SCHEMA public TO enamel_portal;
GRANT USAGE ON SCHEMA public TO enamel_portal_auth;
GRANT USAGE ON SCHEMA public TO enamel_maint;
GRANT USAGE ON SCHEMA public TO enamel_admin;

-- Tables created *after* this script runs (the migrations) are granted to
-- the runtime roles as well, so a later table is not silently unreachable.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO enamel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO enamel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO enamel_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO enamel_worker;

-- The table privileges of an already migrated database. On a fresh volume
-- the tables do not exist yet; the migration grants them when it runs.
DO $$
BEGIN
    IF to_regclass('public.patients_patient') IS NULL THEN
        RETURN;
    END IF;

    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_app';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_app';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_worker';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_worker';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON core_jobrequest TO enamel_platform';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_platform';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_maint';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_maint';

    -- The platform-admin service: owner-level access (it switches to the
    -- owner at connection time), so it needs the data privileges of the
    -- owner role.
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO enamel_admin';
    EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO enamel_admin';

    -- The two patient roles. They are deliberately narrow.
    EXECUTE 'GRANT SELECT ON clinics_clinic TO enamel_portal';
    EXECUTE 'GRANT SELECT ON scheduling_appointmenttype TO enamel_portal';
    EXECUTE 'GRANT SELECT, UPDATE ON clinics_provider TO enamel_portal';
    EXECUTE 'GRANT SELECT ON clinics_location TO enamel_portal';
    EXECUTE 'GRANT SELECT, INSERT ON booking_bookingpolicy TO enamel_portal';
    EXECUTE 'GRANT SELECT ON patients_patient TO enamel_portal';
    EXECUTE 'GRANT SELECT ON patients_allergy TO enamel_portal';
    EXECUTE 'GRANT SELECT ON patients_medicalhistory TO enamel_portal';
    EXECUTE 'GRANT SELECT ON patients_encounter TO enamel_portal';
    EXECUTE 'GRANT SELECT ON messaging_consent TO enamel_portal';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON scheduling_appointment TO enamel_portal';
    EXECUTE 'GRANT SELECT, INSERT ON scheduling_appointmentevent TO enamel_portal';
    EXECUTE 'GRANT SELECT ON plans_treatmentplan TO enamel_portal';
    EXECUTE 'GRANT SELECT, UPDATE ON plans_planitem TO enamel_portal';
    EXECUTE 'GRANT SELECT ON billing_invoice TO enamel_portal';
    EXECUTE 'GRANT SELECT ON billing_payment TO enamel_portal';
    EXECUTE 'GRANT SELECT ON plans_document TO enamel_portal';
    EXECUTE 'GRANT INSERT ON core_auditevent TO enamel_portal';
    EXECUTE 'GRANT SELECT, INSERT, DELETE ON core_jobrequest TO enamel_portal';
    EXECUTE 'GRANT SELECT, UPDATE, INSERT ON portal_patientsession TO enamel_portal';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON portal_portalotpchallenge TO enamel_portal';

    EXECUTE 'GRANT SELECT ON clinics_clinic TO enamel_portal_auth';
    EXECUTE 'GRANT SELECT ON patients_patient TO enamel_portal_auth';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON portal_portalotpchallenge TO enamel_portal_auth';
    EXECUTE 'GRANT INSERT ON portal_patientsession TO enamel_portal_auth';
    EXECUTE 'GRANT INSERT ON core_auditevent TO enamel_portal_auth';

    IF to_regclass('public.core_auditevent_id_seq') IS NOT NULL THEN
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE core_auditevent_id_seq TO enamel_portal';
        EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE core_auditevent_id_seq TO enamel_portal_auth';
    END IF;
END
$$;
