"""Celery beat tasks: scanning, reclamation and housekeeping (T104).

Every entry point here is a thin wrapper over the plain functions in
:mod:`apps.core.jobs` so the beat path and the unit-test path cannot diverge.
``scan_due_actions`` dispatches ``core.jobs.run_job``, which is the same
function an eager test calls directly.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .idempotency import purge_expired
from .jobs import cleanup_jobs, dispatch_due_jobs, due_jobs, reclaim_stale_jobs

logger = logging.getLogger("enamel.jobs")

TASK_SCAN_DUE = "core.tasks.scan_due_actions"
TASK_CLEANUP = "core.tasks.cleanup_expired"
TASK_HEARTBEAT = "core.tasks.heartbeat"
TASK_RECLAIM = "core.tasks.reclaim_stale"


@shared_task(name=TASK_SCAN_DUE)
def scan_due_actions(limit: int | None = None) -> dict[str, Any]:
    """Every 60 s: reclaim stale claims, then dispatch everything now due."""

    reclaimed = reclaim_stale_jobs()
    due = due_jobs(limit=limit)
    dispatched = dispatch_due_jobs(limit=limit)
    logger.info(
        "scan_due_actions",
        extra={"reclaimed": reclaimed, "dispatched": dispatched, "outcome": "ok"},
    )
    return {"reclaimed": reclaimed, "due": len(due), "dispatched": dispatched}


@shared_task(name=TASK_CLEANUP)
def cleanup_expired() -> dict[str, Any]:
    """Daily: purge expired idempotency records and finished job rows."""

    idempotency_rows = purge_expired()
    jobs = cleanup_jobs()
    logger.info(
        "cleanup_expired",
        extra={
            "idempotency_rows": idempotency_rows,
            "done_jobs": jobs["done_jobs"],
            "failed_jobs": jobs["failed_jobs"],
            "outcome": "ok",
        },
    )
    return {"idempotency_records": idempotency_rows, **jobs}


@shared_task(name=TASK_HEARTBEAT)
def heartbeat() -> dict[str, Any]:
    """Every 5 min: prove the worker/beat pair is alive without leaking data."""

    return {
        "status": "ok",
        "service": getattr(settings, "SERVICE_NAME", "enamel-worker"),
        "at": timezone.now().isoformat(),
    }


@shared_task(name=TASK_RECLAIM)
def reclaim_stale() -> dict[str, Any]:
    """Every 5 min: return jobs orphaned by a dead worker to the queue."""

    reclaimed = reclaim_stale_jobs()
    logger.info("reclaim_stale", extra={"reclaimed": reclaimed, "outcome": "ok"})
    return {"reclaimed": reclaimed}


__all__ = [
    "TASK_CLEANUP",
    "TASK_HEARTBEAT",
    "TASK_RECLAIM",
    "TASK_SCAN_DUE",
    "cleanup_expired",
    "heartbeat",
    "reclaim_stale",
    "scan_due_actions",
]
