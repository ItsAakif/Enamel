-- Supabase role bootstrap (owner-run, after the migrations).
--
-- Run this AS the Supabase project owner (the `postgres` role you received at
-- project creation), through the session pooler, AFTER
-- `python manage.py migrate` has run: the migrations create the runtime roles,
-- their attributes, the memberships and all grants/policies. This file does
-- NOT create or drop anything except passwords and `search_path` values, and
-- it is idempotent.
--
-- Usage (PowerShell, from the repository root; the passwords come from a
-- password manager, never from the shell history of a shared machine):
--
--   psql "<owner-session-pooler-url>?sslmode=require" ^
--     -v app_password="<app-password>" ^
--     -v worker_password="<worker-password>" ^
--     -v maint_password="<maint-password>" ^
--     -f deploy/cloud/supabase_bootstrap.sql
--
-- There are no secrets in this file: the three passwords arrive as psql
-- variables (`:'app_password'` etc.). Refusing to run without all three.
-- psql's `\if` accepts a single boolean condition (no `and`), so the guard
-- nests one check per variable:
--
--   \if :{?app_password}
--     \if :{?worker_password}
--       \if :{?maint_password}
--         \echo "bootstrap: passwords provided as psql variables"
--       \else
--         \echo "error: missing -v maint_password=..."
--         \quit
--       \endif
--     \else
--       \echo "error: missing -v worker_password=..."
--       \quit
--     \endif
--   \else
--     \echo "error: missing -v app_password=..."
--     \quit
--   \endif
\if :{?app_password}
\if :{?worker_password}
\if :{?maint_password}
\echo "bootstrap: passwords provided as psql variables"
\else
\echo "error: missing -v maint_password=..."
\quit
\endif
\else
\echo "error: missing -v worker_password=..."
\quit
\endif
\else
\echo "error: missing -v app_password=..."
\quit
\endif

-- 1. Login passwords for the three roles that open sessions. The demo web
--    container uses `enamel_app`; the external GitHub Actions worker uses
--    `enamel_worker`; the maintenance commands use `enamel_maint`. The other
--    runtime roles (`enamel_platform`, `enamel_portal`, `enamel_portal_auth`)
--    are NOLOGIN group roles and get no passwords here.
alter role enamel_app password :'app_password';
alter role enamel_worker password :'worker_password';
alter role enamel_maint password :'maint_password';

-- 2. Make every extension schema resolvable by the runtime roles. Supabase
--    installs `vector` (and friends) in the `extensions` schema; without this,
--    the first unqualified `vector` reference from a runtime role fails.
alter role enamel_app set search_path = public, extensions;
alter role enamel_worker set search_path = public, extensions;
alter role enamel_maint set search_path = public, extensions;
alter role enamel_portal set search_path = public, extensions;
alter role enamel_portal_auth set search_path = public, extensions;

-- 3. Verify. Expect:
--    - `enamel_app`, `enamel_worker`, `enamel_maint` LOGIN and every
--      enamel role NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE;
--    - the public tenant tables with `relrowsecurity` AND
--      `relforcerowsecurity` both true.
select rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
from pg_roles
where rolname like 'enamel%'
order by rolname;

select relname, relrowsecurity, relforcerowsecurity
from pg_class
where relname in ('patients_patient', 'clinics_membership', 'billing_payment')
order by relname;

-- 4. Smoke: connect afterwards with the `enamel_app` session-pooler URL and
--    confirm `select current_user;` is `enamel_app` and that a tenant table
--    returns 0 rows without a clinic context (row-level security in force).
--    See docs/deployment/DEPLOYMENT_GUIDE.md for that command.
