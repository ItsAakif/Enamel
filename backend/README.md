# Enamel backend (Django 6.1)

Runnable foundation for the Enamel API: split settings, PostgreSQL 17 + pgvector,
Redis, health/readiness endpoints, JSON request logging, and the pytest harness.

## Prerequisites

- Python 3.14 (the repo virtualenv at `.venv/` already has the pinned packages)
- Docker Desktop / Docker Engine with Compose
- No SQLite: PostgreSQL is required in every environment.

## Start infrastructure

From the repository root:

```powershell
docker compose -f deploy/docker-compose.yml up -d db redis
docker compose -f deploy/docker-compose.yml ps
```

The stack exposes PostgreSQL on `127.0.0.1:5433` and Redis on `127.0.0.1:6380`.
Compose project name is `enamel`; data lives in named volumes.

## Configure

Copy the example environment file (optional - dev settings already default to the
compose credentials):

```powershell
Copy-Item env.example .env
```

Key variables:

| Variable | Development default |
|---|---|
| `DATABASE_URL` | `postgresql://enamel:enamel_dev_password@127.0.0.1:5433/enamel` |
| `REDIS_URL` | `redis://127.0.0.1:6380/0` |
| `SECRET_KEY` | insecure dev-only placeholder; required in prod |
| `DEBUG` | `true` in `config.settings.dev` |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` |
| `CSRF_TRUSTED_ORIGINS` | local Vite/Django origins |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` |
| `MEDIA_ROOT` | `backend/media` |
| `TIME_ZONE` | `Asia/Kolkata` |
| `DEFAULT_CURRENCY` | `INR` |

## Run

From `backend/`:

```powershell
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py seed_demo
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Endpoints:

- `GET /healthz` -> `{"status":"ok","service":"enamel-api"}` (process only, no DB).
- `GET /readyz` -> `{"status":"ok","checks":{"db":"ok","redis":"ok","extensions":[...]}}`
  or `503` with per-check error codes. Never includes credentials or hostnames.
- `GET /admin/` -> Django admin (operator surface).
- `/api/v1/` -> the versioned REST API (auth, clinics, patients, scheduling,
  charting, plans, billing, portal, AI, ML, imaging, interop, RCM, marketing,
  analytics).

Tenant tables use PostgreSQL row-level security. The request middleware opens
one transaction per request and sets `app.current_clinic_id` and
`app.current_user_id`; Celery jobs set the same context. Migrations and
management commands take an explicit `bypass_rls()` and are never silently
unscoped. See `apps/core/rls.py` and `apps/core/tests/test_rls.py`.

**Row-level security is enforced at runtime.** The policies are enabled and
forced in the schema **and in force**: the `api` service connects as the
non-superuser role `enamel_app` and the `worker`/`beat` services as
`enamel_worker`, so the principal-scoped policies narrow every
query. The runtime roles are `NOSUPERUSER`/`NOBYPASSRLS` without
`CREATEDB`/`CREATEROLE`, and they are not members of the owner or of any
bypass holder. The patient portal runs under its own patient-scoped role
(`enamel_portal`, and `enamel_portal_auth` for the one-time-code bootstrap),
the platform maintenance commands run as `enamel_maint` (with the owner, the
only roles that hold the maintenance bypass - the bypass is a privilege, not a
settable flag), and the migrations run as the owner in a separate compose
service. The deny-by-default matrix (101 proofs in
`apps/core/tests/test_rls*.py`) asserts the isolation against those real
roles; the rest of the suite also runs under a non-superuser test fixture
role, which proves the code paths work without superuser rights but **not**
tenant isolation. See the public architecture note
[`docs/architecture/principal-scoped-rls.md`](../docs/architecture/principal-scoped-rls.md)
and [`docs/deployment/DEPLOYMENT_GUIDE.md`](../docs/deployment/DEPLOYMENT_GUIDE.md)
for the topology, the verification commands and the rollback.

## Tests and lint

```powershell
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check .
```

`config.settings.test` uses the real PostgreSQL/Redis containers, eager Celery,
fast password hashers and a temporary `MEDIA_ROOT`. pytest-django creates and
drops `test_enamel`.

## Accounts

`accounts.User` is the minimal custom user for the first migration: UUIDv7 primary
key, unique email as `USERNAME_FIELD`, `full_name`, `is_active`, `is_staff`,
`is_superuser`, `date_joined`, `last_login`. Create an admin non-interactively:

```powershell
$env:DJANGO_SUPERUSER_EMAIL="owner@example.com"
$env:DJANGO_SUPERUSER_PASSWORD="change-me-please-123"
..\.venv\Scripts\python.exe manage.py createsuperuser --noinput
```

## Sessions

Session cookie is `enamel_session` (httpOnly, SameSite=Lax, Secure in prod).
`SESSION_COOKIE_AGE=43200` is the 12-hour absolute lifetime and
`SESSION_SAVE_EVERY_REQUEST=True` refreshes it. The 30-minute idle limit from
ADR-007 is enforced in T101 (server-side last-activity tracking/rotation); the
cookie settings here are the foundation.

## Imaging archive and de-identification

A DICOM upload is de-identified before it is stored. The tag list comes from
`IMAGING_DEID_TAGS` (see `config/settings/base.py`); the removed keyword names
are recorded on the instance, never their values, and the SHA-256 of the pixel
data is taken before and after the edit so the pixel payload can be proven
unchanged.

### Known limitations

These are properties of the current design, not defects, and they have to be
disclosed to whoever uploads a study:

- **Burned-in annotations are not removed.** Text or graphics rendered into the
  pixel data itself (a patient name burnt into the corner of an image, a
  scanned referral letter) survive de-identification, because the pixel data is
  never touched. The **source system has to de-identify the image** before it
  is uploaded; Enamel cannot strip pixels.
- **Nested sequences are not scrubbed by the default tag list.** The default
  list removes top-level attributes only. A sequence element such as
  `OtherPatientIDsSequence` or `ReferencedPatientSequence` keeps its content
  unless the operator extends `IMAGING_DEID_TAGS` with the matching sequence
  keywords.

The assistive analysis on top of the archive is a transparent
intensity-threshold baseline evaluated on synthetic fixtures: a fixture-only
evaluation, not clinically validated, and it makes no diagnostic claim. Its
findings are recorded as suggestions and are never written to the chart.

## Layout

```
backend/
|-- manage.py
|-- config/            # settings, urls, wsgi/asgi, celery, logging
|-- apps/
|   |-- core/          # tenancy, RLS, audit, health, request-id, errors
|   |-- accounts/      # custom user, sessions, MFA, API keys
|   |-- clinics/       # clinics, locations, memberships, providers
|   |-- patients/      # patients, encounters, medical history, retention
|   |-- charting/      # odontogram states and events
|   |-- scheduling/    # types, availability, appointments, waitlist
|   |-- booking/       # public booking, holds, signed links
|   |-- messaging/     # consent, templates, adapters, scheduled actions
|   |-- plans/         # treatment plans, consent, documents
|   |-- billing/       # invoices, payments, refunds, gateway adapters
|   |-- portal/        # patient identity and portal API
|   |-- ai/            # providers, prompts, tools, voice, RAG
|   |-- ml/            # no-show model registry and inference
|   |-- imaging/       # studies, findings, DICOMweb, inference jobs
|   |-- interop/       # FHIR facade and PMS connectors
|   |-- rcm/           # payers, eligibility, claims, remittance
|   |-- marketing/     # segments, campaigns, recipients
|   `-- analytics/     # metric registry, reports, exports
|-- tests/             # cross-app tenancy, permissions, audit, workflows
|-- scripts/           # seed, evals, model training
|-- ml/                # offline no-show and CV pipelines
|-- pyproject.toml     # ruff + pytest config
`-- requirements.txt   # exact pins

deploy/
|-- docker-compose.yml # db (5433) + redis (6380); prod profile adds app roles
|-- backend.Dockerfile
|-- web.Dockerfile
|-- nginx.conf
|-- smoke.ps1
|-- backup.ps1
|-- restore.ps1
`-- env.example
```
