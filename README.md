# Enamel

Enamel is a multi-tenant dental practice platform for small clinics: scheduling
and public booking, patient records and charting, treatment plans and consent,
imaging review, billing, a patient portal and analytics. It is built as a
portfolio project with Django, React and PostgreSQL, and it includes an
evaluated AI assistant and retrieval layer.

> **Status:** portfolio/demo software. It uses synthetic data and sandbox
> integrations, is **not clinically validated**, and must not be used with real
> patient data.

**Live demo:** [enamel.aakifmudel.xyz](https://enamel.aakifmudel.xyz)

[![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Django 6.1](https://img.shields.io/badge/Django-6.1-092E20?logo=django&logoColor=white)](https://www.djangoproject.com/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)

![Enamel staff dashboard with synthetic demo data](docs/images/dashboard.png)

*The staff workspace with synthetic demo data.*

## What it does

| Workflow | Included |
|---|---|
| Front desk | Provider and operatory scheduling, availability rules, public booking with holds, waitlist offers, reminders and recall |
| Clinical | Patient records, allergies and medical history, encounters, FDI odontogram, imaging archive and previews, treatment plans, consent and documents |
| Revenue | Invoices, payments and refunds with a sandbox gateway, receipt documents, insurance/RCM behind a feature flag |
| Patient | A separate patient portal with one-time-code sign-in, own appointments, records, plans, invoices and documents, plus a PWA shell |
| Operations | Tenant-scoped analytics, an append-only audit chain, durable jobs, and PostgreSQL row-level security |
| AI | A tool-calling assistant with identity and confirmation gates, grounded retrieval with citations, an offline evaluation suite, a no-show model and a fixture-only imaging heuristic |

External providers are **not live** in this repository. Payments use a sandbox,
messaging and e-signature use local/dev adapters, the PMS connector is a
simulator, the voice engine is a simulator, and the AI provider defaults to a
deterministic mock. The patient-portal one-time code is handled by a dev adapter
(it logs the code) or shown explicitly in demo mode.

## Architecture

```mermaid
flowchart LR
    B[Browser] --> SPA[React SPA]
    SPA -->|/api + session cookie + CSRF| API[Django + DRF]
    API --> PG[(PostgreSQL 17 + pgvector + RLS)]
    API --> R[(Redis)]
    API --> M[(Media storage)]
    API --> AI[OpenRouter or mock]
    API -. enqueue .-> R
    W[Celery worker] --> PG
    W --> R
    Beat[Celery beat] --> R
```

In the production-shaped stack, the API runs as the non-superuser `enamel_app`
role and the worker as `enamel_worker`; PostgreSQL row-level security is enabled
and forced, and migrations run as the owner in a separate process. The platform
admin is a separate service on a loopback port and is not part of the public web
tier.
See [`docs/deployment/DEPLOYMENT_GUIDE.md`](docs/deployment/DEPLOYMENT_GUIDE.md)
for the deployment topology and [`backend/README.md`](backend/README.md) for the
backend layout.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, Django 6.1, Django REST Framework, drf-spectacular |
| Data | PostgreSQL 17 + pgvector, Redis 7 |
| Background | Celery worker and beat, durable `JobRequest` rows in PostgreSQL |
| Frontend | React 19, Vite, TypeScript, Tailwind CSS, TanStack Query |
| AI/ML | Provider abstraction (OpenRouter or deterministic mock), optional local ONNX embeddings, scikit-learn |
| Imaging | pydicom, Pillow, DICOM archive with de-identification |
| Delivery | Docker Compose, nginx, gunicorn, GitHub Actions |

## Quick start

Requirements: Python 3.14, Node.js 24 with pnpm 11, and Docker.

```powershell
# 1. Database and Redis (host ports 5433 and 6380)
docker compose -f deploy/docker-compose.yml up -d db redis

# 2. Backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py seed_demo
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

On macOS/Linux, activate the environment and use `python manage.py ...` in the
same order.

```powershell
# 3. Frontend (second terminal, repository root)
pnpm install
pnpm --filter @workspace/enamel-landing run dev
```

Open <http://127.0.0.1:5173>. The Vite dev server proxies `/api` to Django.

The seeded demo owner is `owner@enamel.demo` with password
`EnamelDemo!2026`; `dentist@enamel.demo`, `reception@enamel.demo` and
`assistant@enamel.demo` use the same password. All data is synthetic.

## Configuration

Copy the example file for the delivery mode you are using:

- Local development: [`env.example`](env.example) (optional; `config/settings/dev.py`
  has loopback defaults).
- Production-shaped Compose: [`deploy/env.example`](deploy/env.example).

Production settings fail closed without `SECRET_KEY`, `ALLOWED_HOSTS`, the two
webhook secrets and a valid `CONNECTOR_KEY`. Never commit `.env` files; they are
ignored. `deploy/.env` is the only file the Compose stack reads.

## Development and testing

Run from `backend/` unless noted.

```powershell
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py makemigrations --check
..\.venv\Scripts\python.exe -m ruff check .
```

```powershell
# repository root
python scripts\check_rls.py
pnpm run typecheck
pnpm run build
pnpm --filter @workspace/enamel-landing run test
pnpm run codegen:check
```

The end-to-end suite needs the running stack:

```powershell
$env:E2E_BASE_URL = "http://localhost:8090"
pnpm exec playwright test
```

The production-shaped Compose stack is described in the deployment guide;
operator-only smoke helpers stay private. Machine-learning, retrieval and
evaluation scripts live under the backend's `scripts/` directory.

## Deployment

The public demo is live at <https://enamel.aakifmudel.xyz> on the free-tier
Render/Supabase/Upstash stack. The [deployment guide](docs/deployment/DEPLOYMENT_GUIDE.md)
covers the local Compose path and the hosted setup. This is a portfolio demo,
not a production deployment: media is ephemeral unless the optional object-store
backend is configured, and the background worker runs only in a bounded window.

## AI capabilities

The assistant uses a provider abstraction with a deterministic mock by default
and optional external inference. Tool calls are gated by identity, permission
and confirmation checks; retrieval is clinic-scoped and returns cited answers
with an explicit refusal path. The imaging pipeline is an intensity-threshold
baseline over synthetic fixtures, not a trained diagnostic model. No live-model
quality score or clinical performance is claimed.

## Live demo

[Open the live demo](https://enamel.aakifmudel.xyz). It runs on a free-tier
Render/Supabase/Upstash stack with synthetic data, sandbox integrations and the
demo portal code flow. The free web instance sleeps after 15 minutes without
traffic, so the first load may take about a minute. Do not use it with real
patient information.

The local demo remains available through the quick-start above. The seeded
staff login is `owner@enamel.demo` / `EnamelDemo!2026`; all data is synthetic.

## Security and data

- Tenant isolation is enforced in the application layer and by PostgreSQL row-level
  security, with separate runtime roles for the API, worker, patient portal and
  maintenance commands.
- The audit log is append-only and hash-chained; exports and documents are
  tenant-scoped.
- All included records are synthetic. No real patient data, secrets or
  production credentials are part of this repository.
- This project makes no HIPAA, DPDP, PCI or clinical-validation claim. Payments,
  messaging, e-signature, PMS and AI provider integrations are sandbox or
  simulator implementations.

## Documentation

- [User guide](docs/USER_GUIDE.md) and [PWA behaviour](docs/PWA.md)
- [Principal-scoped RLS design](docs/architecture/principal-scoped-rls.md)
- [Asset provenance](docs/evidence/ASSET_PROVENANCE.md)
- [Deployment guide](docs/deployment/DEPLOYMENT_GUIDE.md)
- [OpenAPI contract](backend/openapi/schema.json)

## Licence

Proprietary. All rights reserved. No open-source licence is granted; the
repository is shared as a portfolio artefact. A different licence would be
required before accepting external code contributions.

## Acknowledgements

Built with Django, Django REST Framework, React, Vite, Tailwind CSS, PostgreSQL,
pgvector, Redis, Celery and scikit-learn. The favicon uses the Tabler "dental"
icon (MIT).
