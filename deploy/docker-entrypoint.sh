#!/bin/sh
# Enamel backend container entrypoint.
#
# Shared by every role built from this image:
#   api     gunicorn config.wsgi:application ...
#   worker  celery -A config worker -l info -Q default,reminders,ai,ml,sync
#   beat    celery -A config beat -l info
#   admin   gunicorn config.admin_wsgi:application ...   (the platform admin)
#   ops     python manage.py <command>                    (the maintenance path)
#
# Only the migrate service sets RUN_MIGRATIONS=1, so schema changes are
# applied by exactly one process (ARCHITECTURE.md section 19). The migration
# step always connects with the owner URL (DATABASE_MIGRATION_URL): the
# runtime role has no DDL rights and must never migrate. Database readiness is
# handled by compose healthchecks; the application itself fails fast when the
# database is unreachable.
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    if [ -n "${DATABASE_MIGRATION_URL:-}" ]; then
        echo "entrypoint: applying migrations as the owner"
        DATABASE_URL="${DATABASE_MIGRATION_URL}" python manage.py migrate --noinput
    else
        echo "entrypoint: applying migrations with the current DATABASE_URL"
        python manage.py migrate --noinput
    fi
fi

exec "$@"
