"""Celery application for Enamel.

Durable job state lives in PostgreSQL (``core.JobRequest``, see ARCHITECTURE.md
section 11 and ADR-008), so no result backend is configured: the broker is only
a wake-up signal and a Redis flush delays work instead of losing it.

Queues are declared explicitly so a slow ML job cannot sit in front of
reminders. The beat schedule is read from ``CELERY_BEAT_SCHEDULE`` in Django
settings (``celery.conf.beat_schedule``) rather than edited in the database.
"""

import os
import ssl

from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Keep in sync with apps.core.jobs.TASK_QUEUES.
TASK_QUEUES: tuple[str, ...] = ("default", "reminders", "ai", "ml", "sync")

app = Celery("enamel")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.update(
    task_default_queue="default",
    task_queues=tuple(Queue(name) for name in TASK_QUEUES),
    # A worker that dies mid-task must not lose the message: the row stays
    # ``running`` and the beat reclaim path re-queues it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# Managed Redis providers (Upstash and most hosted offerings) expose TLS-only
# endpoints. Kombu needs the CA requirement declared explicitly for rediss://;
# a plain redis:// URL keeps the previous behaviour.
if str(os.environ.get("REDIS_URL", "")).startswith("rediss://"):
    app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

__all__ = ["TASK_QUEUES", "app"]
