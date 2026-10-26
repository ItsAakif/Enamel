# Principal-scoped row-level security

How Enamel enforces tenant isolation in the database, not just in the
application layer. This is the public explanation of the shipped design: what
the policies protect, which roles they are written for, how the runtime context
is set, how it is verified, and what its limits are. There are no internal
planning references here; everything below can be read from the repository.

## The problem

A multi-tenant clinical database must never show one clinic's rows to another,
even through an application bug, a missed `WHERE` clause, an injected query or
an operator mistake. An application-layer filter is a real control, but it is
one layer deep. Row-level security (RLS) makes the database itself the second
layer: every query against a tenant table is filtered by a policy that looks at
the **row** and the **acting principal**, regardless of what the query says.

Two design constraints shaped the policy set:

- **A principal is more than a clinic.** Staff users, invitation tokens,
  patient-portal sessions, one-time-code challenges and signed payment webhooks
  all act on tenant tables, and several of them act *before* any clinic context
  exists (resolving your own membership to find your clinic, resolving a portal
  session by the hash of its cookie).
- **One shared predicate would mix these roles.** A single "clinic matches"
  policy granted `TO PUBLIC` would govern the patient role by staff rules and
  vice versa. Every policy is therefore written **for a named role**.

## Tenant and principal boundary

Policies are grouped into five classes:

- **T - tenant**: the clinic-scoped rows (`clinic_id = the current clinic`).
- **P - principal**: rows keyed to the acting staff user, the portal session,
  the one-time-code challenge, the portal patient and the idempotency record.
- **C - capability**: the invitation token (`app.current_invitation_token_hash`)
  and the signed payment webhook path.
- **G - global catalogue**: deliberately shared rows (for example the published
  no-show model).
- **X - platform-private**: platform rows that no tenant can read; one table
  accepts inserts only, with `clinic_id IS NULL`.

Two portal-owned tables (the patient sessions and the one-time-code
challenges) have **no tenant policy at all** - only principal policies - so a
portal request can never read another patient's session row, which carries the
session key hash.

## Runtime roles

| Role | Holds what | Used by |
|---|---|---|
| `enamel` (owner) | owns the tables; the only bypass besides maintenance | migrations, in their own compose service |
| `enamel_app` | LOGIN, `NOSUPERUSER NOBYPASSRLS`, no DDL/DCL | the `api` service |
| `enamel_worker` | member of `enamel_app` and `enamel_platform` | the `worker` and `beat` services |
| `enamel_platform` | the worker's cross-clinic job access | group role only, not login |
| `enamel_portal` | patient-scoped policies | portal requests via `SET LOCAL ROLE` |
| `enamel_portal_auth` | the minimal one-time-code bootstrap grants | the unauthenticated bootstrap step |
| `enamel_maint` | the only non-owner holder of the maintenance bypass | platform maintenance commands |

The application runtime roles are not members of the owner or of any bypass
holder, so they can neither escalate to a bypass nor `SET ROLE` into one.

## Context GUCs

The policies read PostgreSQL custom session parameters, all set with
`SET LOCAL` inside the request/job transaction, so nothing survives to the next
request on a pooled connection:

| GUC | Meaning |
|---|---|
| `app.current_clinic_id` | the clinic whose tenant rows are visible; empty matches no tenant row |
| `app.current_user_id` | the acting staff user (audit/debugging; no policy uses it) |
| `app.current_principal_id` | the authenticated staff user, set before the clinic is resolved |
| `app.current_invitation_token_hash` | SHA-256 of a presented invitation token |
| `app.current_portal_key_hash` | SHA-256 of the portal cookie value |
| `app.current_portal_patient_id` | the patient resolved from the session or challenge |
| `app.current_portal_challenge_id` | the presented one-time-code challenge |
| `app.current_portal_contact` | the contact presented to the bootstrap step |
| `app.current_webhook_gateway/_event_id/_reference` | the signed payment webhook's identity triple |
| `app.bypass_rls` | the maintenance bypass (see below) |

Portal requests additionally run under `SET LOCAL ROLE enamel_portal`, so the
role switch, like the context, can never outlive the transaction.

## Owner and maintenance bypass

`app.bypass_rls = 'on'` is honoured by a bypass policy granted **only** to the
table owner and to `enamel_maint`. Migrations run as the owner; seeding,
retention sweeps and audit maintenance run as `enamel_maint`. Setting the GUC
as the application role changes nothing - the bypass is a privilege granted by
membership, not a flag any session can raise.

The tenant tables are declared `FORCE ROW LEVEL SECURITY`, so even the table
owner is subject to policies except through the explicit bypass. A separate
local-demo command can turn the forcing off for a single-role demo stack; it is
for local rehearsal only and must never be used for a real deployment.

## Migration

`backend/apps/core/migrations/0006_principal_scoped_rls.py` creates the roles
and replaces the earlier clinic-only policy set with the role-scoped inventory
above. Its reverse migration restores the previous policy set exactly, so the
rollback is a real inverse. `deploy/db/init/01-enamel-app-role.sql` applies the
matching runtime-role setup in the Compose database; private operator helpers
are not part of the public release.

## Verification

- `backend/apps/core/tests/test_rls.py` plus the other boundary modules prove
  the deny-by-default matrix against the real roles: what an application
  principal with no clinic context sees, what the portal role can and cannot
  read, what the maintenance bypass does and does not cover, and that the
  runtime role cannot widen itself.
- `scripts/check_rls.py` is the release gate: it runs the boundary modules
  (at least 101 collected proofs) and fails if any proof is skipped or the run
  drops below the floor.
- The boundary tests under `backend/apps/core/tests/` fail if a runtime role is
  a superuser or if RLS is not in force on the tenant tables; the operator-only
  smoke helper used by the maintainers is not required for the public release.

## Limits and caveats

- **The boundary assumes the database connection is trustworthy within its
  role.** The context GUCs are set by the application, so RLS defends against
  application bugs, missed filters and injected statements - not against a
  compromised application server talking directly to the database with the
  runtime credential.
- **A few tables are intentionally outside the inventory**: the user identity
  tables (email lookup must work before authentication), the clinic catalogue
  (public by design, no clinical data), and framework tables.
- RLS is one layer of two: the application-level tenant and object scoping
  remains in force and is still what operators should reason about first.
