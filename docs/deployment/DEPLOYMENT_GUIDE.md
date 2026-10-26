# Deployment guide — free split platform

Status: **reference deployment is live at <https://enamel.aakifmudel.xyz>.**
The code and workflow changes described below are in this tree
(`deploy/render/start.sh` gates, S3 media backend, the 50 MB caps,
`deploy/cloud/supabase_bootstrap.sql`, the worker and backup workflows, the
keepalive Worker). Provider state is external to this repository and contains
no credentials. The local production-shaped Compose stack remains the
reproducible local path. Provider facts were checked against official
documentation on **2026-10-25**.

> Do not use this deployment with real patient data. It uses synthetic demo
> data, sandbox integrations and a demo-mode portal code.

## Target architecture

```
Browser
  │ https://<render-service>.onrender.com  (same origin)
  ▼
Render Free web service — one container
  ├─ nginx: built SPA + /api proxy
  ├─ gunicorn: Django API (enamel_app role, RLS in force)
  └─ no worker (ENABLE_WORKER=0)
        │
        ├─ PostgreSQL (TLS, session pooler) ──► Supabase Free (pgvector + RLS)
        ├─ Redis (TLS) ───────────────────────► Upstash Free (Celery broker)
        └─ media (local disk by default; ─────► ephemeral on Render; a private
           optional S3-compatible backend)      Supabase Storage bucket makes
                                                it persistent (see section 9)
Cloudflare Worker Cron (every 10 min) ────────► GET /readyz (keepalive)
GitHub Actions scheduled job ──────────────────► Celery worker + Beat (window)
```

**Why one Render web service for the SPA and API?** The SPA uses session cookies
and CSRF (`credentials: "include"`). If the SPA is on a different origin from the
API (for example a Render Static Site or Cloudflare Pages), browsers treat the
session cookie as third-party and may block it; `SameSite=None` does not fix
that in Safari/Firefox/Chrome incognito. A same-origin service avoids the
problem. A separate static frontend is only viable with a same-origin proxy
(Cloudflare Pages Function/Worker) that forwards cookies, CSRF and bodies, and
that proxy must be tested.

## 1. Create the services in this order

1. **GitHub repository** (public for free Actions; see Worker).
2. **Supabase** project (database + storage).
3. **Upstash** Redis.
4. **Render** web service.
5. **Supabase Storage** bucket (optional; see section 9 - media works on
   local disk without it).
6. **GitHub Actions** worker workflow (`.github/workflows/worker.yml`).
7. **Cloudflare Worker** keepalive (`deploy/cloudflare/keepalive/`).
8. **Backup workflow** (`.github/workflows/backup.yml`) and a restore
   rehearsal.

Do not start with Render: the API cannot boot until the database, roles and
Redis exist.

## 2. Supabase — database and roles

Create a Free project, save the database password, and choose a region close to
Render (Frankfurt pairs with the recommended Render region).

Enable the extensions and verify all four required extensions:

```sql
select name, default_version, installed_version
from pg_available_extensions
where name in ('vector','btree_gist','pgcrypto','pg_trgm');
```

`vector` is documented by Supabase; `btree_gist`, `pgcrypto` and `pg_trgm` are
standard extensions. If any is missing, stop — the migrations need them.

Use the **Session pooler** URI (IPv4, port 5432) for migrations. Direct
connections are IPv6 unless the paid IPv4 add-on is enabled.

```powershell
cd backend
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
$env:DATABASE_URL = "postgresql://postgres.<ref>:<owner-password>@<pooler-host>:5432/postgres?sslmode=require"
$env:SECRET_KEY = "<temporary-random-64>"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:PAYMENT_WEBHOOK_SECRET = "<temporary>"
$env:MESSAGING_WEBHOOK_SECRET = "<temporary>"
$env:CONNECTOR_KEY = "<temporary-fernet-key>"
$env:REDIS_URL = "redis://localhost:6379/0"
..\.venv\Scripts\python.exe manage.py migrate --noinput
```

The migration creates the runtime roles, memberships and table grants. It does
**not** set login passwords for the runtime roles. Set them as the owner with
[`deploy/cloud/supabase_bootstrap.sql`](../../deploy/cloud/supabase_bootstrap.sql)
(use a password manager; never commit). The file (run as the Supabase owner,
through the session pooler):

```powershell
cd ..
psql "<owner-session-pooler-url>?sslmode=require" -v app_password="<app-password>" `
  -v worker_password="<worker-password>" -v maint_password="<maint-password>" `
  -f deploy/cloud/supabase_bootstrap.sql
```

It sets the three login passwords, gives the runtime roles
`search_path = public, extensions` (so the Supabase `extensions` schema stays
resolvable), and verifies the role attributes and forced row-level security.
It contains no secrets and creates or drops nothing.

Connect as the app role through the session pooler:

```
postgresql://enamel_app.<ref>:<app-password>@<pooler-host>:5432/postgres?sslmode=require
```

and verify isolation (run inside a transaction):

```sql
begin;
select current_user;                         -- enamel_app
select count(*) from patients_patient;       -- 0 with no clinic context
set local app.current_clinic_id = '<clinic-uuid>';
select count(*) from patients_patient;       -- only this clinic
set local app.current_clinic_id = '<other-clinic-uuid>';
select count(*) from patients_patient;       -- must be 0
rollback;
```

Seed synthetic demo data as the owner (not the app role):

```powershell
$env:DATABASE_URL = "<owner-session-pooler-url>"
..\.venv\Scripts\python.exe manage.py seed_demo
```

**Do not set `DEMO_SINGLE_ROLE=1`.** That command disables forced RLS; it exists
for the single-role Render demo and is not used here.

## 3. Upstash Redis

Create a Free Redis database, claim it, and copy the TLS URL
(`rediss://default:<password>@<host>:6379`).

```powershell
redis-cli -u "rediss://default:<password>@<host>:6379" ping
```

The application enables Celery broker TLS automatically for `rediss://`. Free
quota: **250 MB and 500 K commands/month**; a continuous Celery poll can exceed
this. See Worker.

## 4. Render web service

Create a **Web Service**, connect the repository, choose **Docker**, and use
`deploy/render/Dockerfile` (the single-container SPA + API image). Plan: Free.
Region: nearest. Health check: `/healthz`.

**Migration boundary (implemented):** `deploy/render/start.sh` runs
`manage.py migrate` **only** when `RUN_MIGRATIONS=1` (default `0`), so the
default web process never holds or uses a migration credential. The schema in
section 2 was applied by the owner before the first boot. Never set
`RUN_MIGRATIONS=1` on the public web service; the runtime must not migrate as
the app role.

Environment variables (placeholders only):

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.demo` for the demo host; `config.settings.prod` for a non-demo staging |
| `DEMO_MODE` | `true` on the demo only; production refuses it |
| `SECRET_KEY` | `<random>` |
| `ALLOWED_HOSTS` | `<service>.onrender.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://<service>.onrender.com` |
| `CORS_ALLOWED_ORIGINS` | empty for same-origin; set only for a tested proxy/direct split |
| `FRONTEND_BASE_URL` | `https://<service>.onrender.com` |
| `SECURE_SSL_REDIRECT` | `true` |
| `DATABASE_URL` | `postgresql://enamel_app.<ref>:<app-password>@<pooler-host>:5432/postgres?sslmode=require` |
| `REDIS_URL` | `<upstash rediss URL>` |
| `PAYMENT_WEBHOOK_SECRET` | `<random>` |
| `MESSAGING_WEBHOOK_SECRET` | `<random>` |
| `CONNECTOR_KEY` | `<Fernet key>` |
| `PORTAL_OTP_LOG_PATH` | empty string (no OTP codes at rest) |
| `RUN_MIGRATIONS` | `0` (image default; see above) |
| `ENABLE_WORKER` | `0` |
| `DEMO_SINGLE_ROLE` | `0` |
| `AI_PROVIDER` / `AI_MOCK_MODE` | `mock` / `true` unless OpenRouter is configured |
| `AI_EMBEDDING_PROVIDER` | `mock` for the 512 MB instance; `fastembed` is heavier |
| `AWS_STORAGE_BUCKET_NAME` | unset (local media) for the demo; see section 9 |

Optional S3 media variables (only when a bucket is configured; see section 9):
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`,
`AWS_S3_REGION_NAME`. With the bucket set but any of these missing, the
process refuses to start with `ImproperlyConfigured`.

Never put `DATABASE_MIGRATION_URL` in Render: the runtime must not hold the
owner credential.

Deploy and check:

```powershell
curl.exe -fsS https://<service>.onrender.com/healthz
curl.exe -fsS https://<service>.onrender.com/readyz
```

Render Free sleeps after 15 minutes idle and takes about a minute to wake; see
the keepalive in section 8. It has 750 instance-hours/month and no persistent
disk.

## 5. Frontend URL, CORS, CSRF, cookies

The SPA calls relative `/api`; `VITE_API_BASE_URL` is optional and defaults to
same-origin (`artifacts/enamel-landing/src/lib/api.ts`). With the single Render
service, no frontend change is required.

- **Cookies:** the session cookie is `HttpOnly`, `Secure` and `SameSite=Lax`;
  CSRF uses the double-submit cookie and `credentials: "include"` is already in
  the generated client.
- **CSRF:** set `CSRF_TRUSTED_ORIGINS` to the exact public origin (scheme + host).
- **Allowed hosts:** set `ALLOWED_HOSTS` to the Render hostname. If a proxy
  forwards the original Host, add the public hostname too.
- **Proxy headers:** `SECURE_PROXY_SSL_HEADER` trusts `X-Forwarded-Proto`; a
  proxy must set it to `https`. The Render nginx template maps this header
  through instead of overwriting it with the container's own scheme, so with
  `SECURE_SSL_REDIRECT=true` Django sees `https` and does not redirect-loop
  (and plain-HTTP local testing of the image still works).
- **Separate static frontend:** do not point a Render Static Site or Pages
  project directly at the API. Use a same-origin proxy (Pages Function/Worker)
  that preserves `Set-Cookie`, `Origin`, `X-CSRFToken` and streaming bodies, and
  test login/CSRF before relying on it. Otherwise use the single web service.

Public booking (`/book/<slug>`), the staff workspace (`/app/*`) and the portal
(`/portal/*`) are client-side routes and work unchanged on the same origin.

## 6. OpenRouter (optional)

Set the key as a Render secret, never in the repository:

- `OPENROUTER_API_KEY=<key>`
- `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`
- `AI_PROVIDER=openrouter`
- `AI_MODEL=<free-model-id>` and `AI_FALLBACK_MODEL=<free-model-id>`
- `AI_ALLOW_PAID=false`
- `AI_EMBEDDING_PROVIDER=mock` (or `fastembed` if the instance has memory; or
  `openrouter` only if an embedding model is configured)

The committed `backend/apps/ai/evals/configs/free-models.json` pin is
**provisional** (`unverified-under-load`). The free pool was rate-limited on
2026-10-24; do not claim measured live-model quality. Re-run:

```powershell
cd backend
python scripts\ai_model_bench.py run --recheck --out artifacts/evals
```

The benchmark refuses paid models unless explicitly overridden; leave
`AI_ALLOW_PAID=false`.

## 7. Worker and scheduler — bounded, not continuous

Render Free has no worker. `.github/workflows/worker.yml` implements a
scheduled, bounded Celery worker + Beat window (free for a **public**
repository; 2,000 minutes/month for private; 6-hour job maximum):

Run it from `backend/` with the API's settings and secrets, swapping only the
database credential for the worker role (`secrets.WORKER_DATABASE_URL`):

```bash
cd backend
celery -A config worker -B -l info \
  -Q default,reminders,ai,ml,sync \
  --pool=solo --concurrency=1 --max-tasks-per-child=50
```

The workflow runs exactly this command env: `DJANGO_SETTINGS_MODULE`
(`config.settings.demo`), `DEMO_MODE=true`, `ALLOWED_HOSTS`, the six secrets
(`WORKER_DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET`,
`MESSAGING_WEBHOOK_SECRET`, `CONNECTOR_KEY`), the mock AI settings and an
empty `PORTAL_OTP_LOG_PATH`. It is bound by `timeout-minutes: 240` (a 4-hour
window), runs on a daily cron plus `workflow_dispatch`, holds a concurrency
group so overlapping windows queue instead of cancel, and grants
only `contents: read`. It must hold `secrets.WORKER_DATABASE_URL` (the `enamel_worker` session-pooler
URL) - never an owner URL.

- Environment: reuse the Render service's `DJANGO_SETTINGS_MODULE`,
  `ALLOWED_HOSTS`, `SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET`,
  `MESSAGING_WEBHOOK_SECRET`, `CONNECTOR_KEY` and `AI_*` values; add
  `DEMO_MODE=true` for the demo. The workflow names the database secret
  `WORKER_DATABASE_URL` (the `enamel_worker` session-pooler URL, mapped to
  `DATABASE_URL`) and `REDIS_URL` from Upstash. Leave `PORTAL_OTP_LOG_PATH`
  empty.
- Run it in a **bounded daily window** (start with 4 hours; a 4-hour window is
  ≈14,400 Upstash commands/day ≈432 K/month, inside the 500 K free allowance).
- Jobs enqueued outside the window are not processed until the next window.
  The broker queue is Redis; some staff escalations are also recorded as durable
  `JobRequest` rows. Reminders, recall, waitlist sweeps and long AI jobs can be
  delayed up to the window interval. This is a documented demo limitation, not a
  continuous scheduler.
- Beat runs only inside the window; missed schedules are not backfilled. If
  continuous Beat is required, use a 1 GB VM (for example GCP e2-micro, card
  required) or a paid worker.
- A private repository is limited to 2,000 Actions minutes/month; use a 1-hour
  daily window in that case.

Measure Upstash command usage after the first run and reduce the window if the
projection exceeds the free allowance.

## 8. Keepalive

`deploy/cloudflare/keepalive/` implements the Cloudflare Worker already: a
cron trigger every 10 minutes that fetches `KEEPALIVE_URL` with an
`x-keepalive: 1` header and logs any non-2xx answer (`src/index.js`);
`wrangler.toml` carries the trigger and a placeholder public URL. Deploy it:

```powershell
npm install --location=global wrangler
wrangler login
# edit deploy/cloudflare/keepalive/wrangler.toml: KEEPALIVE_URL
wrangler deploy   # run this inside deploy/cloudflare/keepalive/
```

Set `KEEPALIVE_URL=https://<service>.onrender.com/readyz`. This keeps Render
awake (within 750 h/month) and prevents the Supabase Free project from pausing
after a week of inactivity. `/readyz` checks the database, Redis and required
PostgreSQL extensions. No account identifiers are stored in the repository.
Disabling the trigger is part of the rollback path in section 11.

## 9. Media storage and upload limits

**By default media stays on the local disk** (`MEDIA_ROOT`); Render Free has
an ephemeral filesystem, so DICOM studies and generated PDFs are lost on
restart. Treat local-disk media as synthetic demo data only.

An S3-compatible backend is **implemented** in `backend/config/settings/prod.py`
and is opt-in via environment:

1. Create a private Supabase Storage bucket (`enamel-media`).
2. Generate the S3 access keys (Storage → S3; server-only, they bypass RLS).
3. Add these Render environment variables: `AWS_STORAGE_BUCKET_NAME`,
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL` (and
   `AWS_S3_REGION_NAME`; Supabase documents the region in its Storage S3
   settings). `backend/requirements.txt` already carries
   `django-storages[s3]` and `boto3`.
4. The settings configure the standard backend contract for these values:
   `AWS_QUERYSTRING_AUTH=True` (signed, private download URLs, `s3v4`
   signatures), `AWS_S3_ADDRESSING_STYLE="path"` (what Supabase Storage's S3
   endpoint expects), `AWS_S3_FILE_OVERWRITE=False`, `AWS_DEFAULT_ACL=None`.
   If the bucket is set but the endpoint or the keys are missing, the process
   refuses to start with `ImproperlyConfigured` instead of silently writing
   media to the ephemeral disk.
5. With no `AWS_STORAGE_BUCKET_NAME`, the local `FileSystemStorage` default is
   untouched — the local Compose stack and the test suite are unchanged — and
   the staticfiles backend always stays whitenoise either way.

The upload cap is **50 MB** everywhere (the Supabase Free per-object limit):
`IMAGING_MAX_BYTES` / `DEFAULT_MAX_BYTES` in
`backend/apps/imaging/services/ingest.py`, `client_max_body_size 50m` in
`deploy/nginx.conf` (local Compose) and in
`deploy/render/nginx.conf.template` (Render). An over-cap upload is refused
with the 413 `payload_too_large` error (
`backend/apps/imaging/tests/test_size_cap.py`).

Free Supabase Storage is 1 GB with a 50 MB object limit; there is no
independent media backup in the 1 GB quota. Test upload, preview and signed
download, and confirm the object is not public, when the bucket is
configured.

## 10. Database backups

Supabase Free has **no automatic backups**. `.github/workflows/backup.yml`
implements a nightly (and manual) dump: `pg_dump --format=custom` inside a
`postgres:17` container against `secrets.SUPABASE_DB_URL` (the owner session
pooler; the URL is never echoed or stored in the tree), a SHA-256 sidecar, and
an `actions/upload-artifact` upload with 7-day retention. If the secret is
missing the workflow fails with a clear error instead of pretending a backup
ran.

**A public-repository Actions artifact is publicly downloadable: that is only
acceptable while the database holds synthetic demo data. For real data, move
the artifact upload to a private Supabase Storage bucket target in the
workflow.**

Before the first migration, take a manual dump using the owner session pooler:

```powershell
pg_dump "<owner-session-pooler-url>" --format=custom --file=enamel.dump
```

Rehearse a restore into a scratch Postgres (never over the live database):

```powershell
docker run --rm -e POSTGRES_PASSWORD=scratch -p 5544:5432 -d postgres:17
$env:PGPASSWORD="scratch"
pg_restore -h localhost -p 5544 -U postgres -d postgres --clean --if-exists enamel.dump
```

Then compare row counts and run `python manage.py audit_verify` against the
scratch database. The media bucket has no independent backup on the free plan;
document this.

## 11. Health checks, smoke tests and rollback

Health:

```powershell
curl.exe -fsS https://<service>.onrender.com/healthz
curl.exe -fsS https://<service>.onrender.com/readyz
```

The maintainers' Compose smoke helper is **operator-only** and is not part of
the public release. For the hosted demo, use the HTTP checks above and the
manual acceptance list below; `/healthz`, `/readyz`, the SPA root, the
`/admin/` 404 and the CSRF → login → `/api/v1/auth/me/` flow are the same
checks those scripts exercise.

For the acceptance list beyond those automated probes:

- Staff login with the seeded owner; clinic switcher; dashboard data.
- Patient list/detail; odontogram persistence after reload.
- Scheduling create/reschedule/cancel; 409 on conflict.
- Public booking from availability to confirmation.
- Plan + consent + invoice; sandbox payment; receipt.
- Portal demo login (code shown in demo mode); only the demo patient's data.
- Cross-tenant id returns 404; no foreign rows.
- Media upload/download: with a bucket configured, upload a synthetic file
  under 50 MB and download a signed URL; without a bucket, media is ephemeral
  and not part of hosted acceptance.
- Dispatch a job and confirm the worker window processes it.
- `/admin/` returns 404; no Postgres/Redis ports are publicly reachable.

Rollback:

- Render → Roll back to a previous deploy from the dashboard.
- Worker/keepalive → disable the GitHub workflow / Cloudflare cron.
- Database → restore the last verified dump into a scratch database, verify,
  then repoint `DATABASE_URL`; never restore over live without a verified dump.
- Media → no independent backup on the free plan; synthetic fixtures can be
  regenerated.

## 12. Free-tier limits (official links, 2026-10-25)

| Service | Limit | Link |
|---|---|---|
| Render Free web | 0.1 CPU / 512 MB, sleeps 15 min, 750 h/month, no disk/worker | <https://render.com/docs/free> |
| Supabase Free | 500 MB DB, 1 GB storage, 50 MB/object, 5 GB egress, 2 projects, pauses after 1 week, no backups | <https://supabase.com/pricing> · <https://supabase.com/docs/guides/storage/uploads/file-limits> |
| Upstash Free | 250 MB, 500 K commands/month, 1 DB, TLS | <https://upstash.com/pricing/redis> |
| GitHub Actions | Public repos free; private 2,000 min/month; 6 h/job | <https://docs.github.com/en/actions/reference/limits> |
| Cloudflare Workers | 100 K req/day, 10 ms CPU, 5 cron triggers | <https://developers.cloudflare.com/workers/platform/limits/> |
| Cloudflare Pages | 500 builds, 20 K files, 25 MiB/asset (only for the optional split) | <https://developers.cloudflare.com/pages/platform/limits/> |

## 13. Local vs live verification

| Check | Status |
|---|---|
| Backend suite, RLS proofs, E2E, smoke, build | **Run locally** in the repository; see the test commands in the README |
| Render single-container image (including the `RUN_MIGRATIONS` gate, the kept `X-Forwarded-Proto`, the 50 MB cap and the nginx one-worker cap) | **Implemented; memory profile measured locally only (guide 13a); not live-verified**; the local production-shaped Compose stack is the verified path |
| S3 media backend | **Implemented behind env (unset by default); not live-verified against Supabase Storage** |
| `deploy/cloud/supabase_bootstrap.sql` | **Implemented; not executed live**; steps above |
| Worker / backup GitHub workflows, keepalive Worker | **Files present; not executed or provisioned live** |
| Upstash Celery command budget | **Not measured live**; worker must be sized after the first run |
| Render deployment | **Not executed** |
| Split frontend proxy | **Not executed**; same-origin is recommended |
| OpenRouter live inference | **Blocked by free-pool quotas**; mock default |

No live deployment or measured live-model performance is claimed.

### 13a. Local memory profile of the single container

Measured locally against the built `deploy/render/Dockerfile` image (idle,
attached to the local Compose Postgres/Redis with the demo settings; **not
measured on Render itself**):

- Before the nginx cap: 17 nginx processes (1 master + 16 `auto` workers)
  and roughly 45 MB of nginx RSS on top of ~335 MB of container memory.
- After the cap (`deploy/render/start.sh` rewrites nginx's
  `worker_processes auto` to `worker_processes 1` at boot): 2 nginx
  processes (master + 1 worker), about 13 MB of nginx RSS, and the idle
  container dropped from ~337 MB to **258-293 MB** across `docker stats`
  samples on the same machine (the latest final-image sample was 258 MiB).

Exact startup configuration in this image (render.yaml matches it):

- gunicorn: **1 worker, 2 threads** (`--timeout 120`)
- nginx: **1 worker** (enforced by `deploy/render/start.sh`)
- no Celery in the container: `ENABLE_WORKER=0` (the external worker is
  `.github/workflows/worker.yml`)
- `RUN_MIGRATIONS=0` (migrations are owner-run once, before first boot)
- `SEED_DEMO=0`, `DEMO_SINGLE_ROLE=0`
- local (ephemeral) media unless the optional S3 bucket env is set

The 258-293 MB idle range is not a safety margin: the 50 MB upload peak memory
was **not measured**, the gunicorn worker RSS alone is ~260 MB, and Render
Free allows 512 MB at 0.1 CPU. The plan is tight/marginal - it is sized for
light demo browsing and is **not claimed safe** for sustained 50 MB ingests
on Render Free. If uploads or worker duty matter on the hosted demo, move to
a 1 GB+ web instance (or a paid plan) rather than trusting the free tier.

## 14. Implemented in this tree (previously "not yet applied")

The plan-section-9 changes exist in the tree now:

- `deploy/render/start.sh`: migrations only with `RUN_MIGRATIONS=1`
  (default `0`, logged when skipped); DB-readiness wait and the
  `SEED_DEMO`/`DEMO_SINGLE_ROLE`/`ENABLE_WORKER` toggles unchanged.
- `backend/requirements.txt` + `backend/config/settings/prod.py`: the optional
  S3 media backend (bucket env unset keeps local storage).
- `deploy/nginx.conf`, `deploy/render/nginx.conf.template`,
  `backend/apps/imaging/services/ingest.py`: the 50 MB upload limit (plus the
  preserved upstream `X-Forwarded-Proto` in the Render template).
- `deploy/cloud/supabase_bootstrap.sql`: the owner-run password and
  `search_path` template (roles/grants/policies come from the migrations).
- `.github/workflows/worker.yml` and `.github/workflows/backup.yml`.
- `deploy/cloudflare/keepalive/` (scheduled Worker, `wrangler.toml`, README).

None of it is deployed, executed live, pushed or provisioned; no provider
resource exists. Keep the local Compose stack working: all of this is additive
and must not disable RLS, authentication or tenant isolation.
