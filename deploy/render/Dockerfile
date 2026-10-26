# Enamel single-container image for Render's free web service.
#
# Build from the repository root:
#   docker build -f deploy/render/Dockerfile -t enamel-render .
#
# One container runs the SPA (nginx on $PORT), gunicorn on 127.0.0.1:8000 and
# one Celery worker with the scheduler embedded. Postgres and Redis are
# external (the documented free stack is Supabase + Upstash). Keeping the
# background tier inside the web process is what makes the free tier viable:
# Render's free plan has no separate worker or cron service.

FROM node:24-slim AS spa

WORKDIR /repo

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY scripts/package.json ./scripts/package.json
COPY lib/api-spec ./lib/api-spec
COPY lib/api-client-react ./lib/api-client-react
COPY lib/api-zod ./lib/api-zod
COPY artifacts/enamel-landing ./artifacts/enamel-landing

RUN pnpm install --frozen-lockfile
RUN pnpm --filter @workspace/enamel-landing run build

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx supervisor gettext-base curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY backend/ ./
COPY --from=spa /repo/artifacts/enamel-landing/dist/public /usr/share/nginx/html
COPY deploy/render/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/render/supervisord.conf /etc/supervisor/conf.d/enamel.conf
COPY deploy/render/start.sh /usr/local/bin/start.sh

RUN chmod 0755 /usr/local/bin/start.sh \
    && rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /app/staticfiles /app/media

# collectstatic only imports settings (it opens no database connection). The
# demo import chain is fail-closed about secrets, so give it throwaway values.
RUN SECRET_KEY=build-only \
    ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgresql://build:build@127.0.0.1:5432/build \
    REDIS_URL=redis://127.0.0.1:6379/0 \
    PAYMENT_WEBHOOK_SECRET=build-only \
    MESSAGING_WEBHOOK_SECRET=build-only \
    CONNECTOR_KEY="$(python -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
    DJANGO_SETTINGS_MODULE=config.settings.demo \
    python manage.py collectstatic --noinput

EXPOSE 8080

CMD ["/usr/local/bin/start.sh"]
