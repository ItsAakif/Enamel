# Enamel backend image. Build from the repository root:
#   docker build -f deploy/backend.Dockerfile -t enamel-api .
# One image serves every role; compose overrides the command:
#   api     CMD (below)                     RUN_MIGRATIONS=1
#   worker  celery -A config worker ...     RUN_MIGRATIONS=0
#   beat    celery -A config beat -l info   RUN_MIGRATIONS=0

FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

RUN groupadd --gid 1001 enamel \
    && useradd --uid 1001 --gid enamel --create-home --shell /bin/bash enamel

# Copied and made executable while still root; the container runs as enamel.
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=enamel:enamel backend/ ./

RUN mkdir -p /app/media /app/staticfiles \
    && chown -R enamel:enamel /app

USER enamel

# Build-time values are only needed to import settings for collectstatic; they
# are not baked into runtime configuration. ALLOWED_HOSTS is required by
# config.settings.prod, so the build gets a loopback-only value. The webhook
# secrets and the connector key are required by the same fail-closed check, so
# the build gets throwaway values: collectstatic never talks to a gateway.
RUN SECRET_KEY=build-only DATABASE_URL=postgresql://build:build@127.0.0.1:5432/build \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    ALLOWED_HOSTS=localhost \
    PAYMENT_WEBHOOK_SECRET=build-only \
    MESSAGING_WEBHOOK_SECRET=build-only \
    CONNECTOR_KEY="$(python -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    python manage.py collectstatic --noinput

EXPOSE 8000

# The probe sends an explicit Host header (Django's ALLOWED_HOSTS check applies
# to 127.0.0.1 requests too). config.settings.prod always appends the loopback
# hostnames, so a strict production ALLOWED_HOSTS cannot mark the container
# unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; req = urllib.request.Request('http://127.0.0.1:8000/healthz', headers={'Host': 'localhost'}); urllib.request.urlopen(req, timeout=3).read()"

# The entrypoint applies migrations only when RUN_MIGRATIONS=1 (the api role)
# and then execs the command, so worker/beat reuse the same image.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Gunicorn access logs are disabled: request logging is structured JSON from
# RequestIdMiddleware and never includes query strings/bodies.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]

# Named target for ``docker compose --profile prod``. Compose builds this stage;
# the worker and beat services reuse the same image with an overridden command.
FROM base AS api
