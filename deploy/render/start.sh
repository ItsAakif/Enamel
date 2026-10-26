#!/bin/sh
# Render container entrypoint.
#
# 1. Render the nginx config for the port Render assigned.
# 2. Generate a connector key when the operator did not supply one (the demo
#    has no live practice-management connector, so a per-boot key is fine).
# 3. Wait for Postgres, apply migrations only when RUN_MIGRATIONS=1, optionally
#    seed the demo data.
# 4. Hand over to supervisord (nginx + gunicorn + Celery).
#
# Migration boundary: the web container connects as the runtime `enamel_app`
# role (never the owner), so it must not run the schema migrations. Set
# RUN_MIGRATIONS=1 only for a one-boot, owner-credentialed bootstrap; keep the
# owner credential out of the web service environment in every other case. On
# the documented free stack (Supabase + GitHub Actions), migrations are applied
# once by an operator before the first boot; see deploy/RENDER.md and
# docs/deployment/DEPLOYMENT_GUIDE.md.
set -eu

: "${PORT:=8080}"
export PORT

# Render Free is 0.1 CPU / 512 MB. nginx's default `worker_processes auto`
# spawns one worker per detected core: measured locally that is a master plus
# 16 workers holding about 60 MB together. A single worker serves the same
# traffic with a small fraction of that, and nothing changes for the caller
# (the master keeps every listener socket open; any core that becomes
# available is still used by the event loop).
if grep -q '^worker_processes auto;' /etc/nginx/nginx.conf; then
    sed -i 's/^worker_processes auto;/worker_processes 1;/' /etc/nginx/nginx.conf
fi

envsubst '${PORT}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf

if [ -z "${CONNECTOR_KEY:-}" ]; then
    export CONNECTOR_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    echo "start: generated a per-boot CONNECTOR_KEY (set it explicitly for a real deployment)"
fi

python - <<'PY'
import os
import time

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.demo"),
)

import django

django.setup()

from django.db import connection

for attempt in range(1, 31):
    try:
        with connection.cursor() as cursor:
            cursor.execute("select 1")
        print("start: database is ready")
        break
    except Exception as exc:  # noqa: BLE001 - startup retry loop
        print(f"start: database not ready ({attempt}/30): {exc}")
        time.sleep(2)
else:
    raise SystemExit("start: database never became ready")
PY

# Migrations are opt-in: the default web process speaks to the database with a
# runtime, non-owner role. Running the migrator from the booting web service
# with that credential would either fail (no DDL grants) or demand that the
# owner URL be placed in the public service environment, so it is skipped
# unless the operator deliberately opts in for a one-off bootstrap.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    echo "start: RUN_MIGRATIONS=1 - applying migrations"
    python manage.py migrate --noinput
else
    echo "start: RUN_MIGRATIONS!=1 - skipping migrations (apply the schema with an owner/migration credential before the first boot)"
fi

# A single-role demo connects as the database owner. Forced RLS would hide the
# principal-scoped rows that login and the portal need, so the demo turns the
# FORCE flag off. The role-scoped policies stay installed and the application
# layer remains tenant-scoped; see deploy/RENDER.md.
if [ "${DEMO_SINGLE_ROLE:-0}" = "1" ]; then
    python manage.py demo_single_role || echo "start: demo_single_role failed"
fi

if [ "${SEED_DEMO:-0}" = "1" ]; then
    python manage.py seed_demo || echo "start: seed_demo failed; continuing with the existing data"
fi

# The free 512 MB instance fits nginx + gunicorn, not a second Django process.
# ENABLE_WORKER=0 (the value in render.yaml) drops the Celery block; set it to
# 1 on a 1 GB+ instance to run reminders, recall and the sweeps.
if [ "${ENABLE_WORKER:-1}" = "0" ]; then
    python - <<'PY'
import re

source = open("/etc/supervisor/conf.d/enamel.conf", encoding="utf-8").read()
source = re.sub(r"; --- celery begin ---.*?; --- celery end ---\n", "", source, flags=re.S)
open("/run/enamel-supervisord.conf", "w", encoding="utf-8").write(source)
PY
    echo "start: ENABLE_WORKER=0 - running without the background worker"
    exec supervisord -c /run/enamel-supervisord.conf
fi

exec supervisord -c /etc/supervisor/conf.d/enamel.conf
