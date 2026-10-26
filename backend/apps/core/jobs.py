"""Durable background jobs (T104, ADR-008).

Pattern
-------

A service writes a :class:`~apps.core.models.JobRequest` row inside its own
transaction and the task is dispatched ``on_commit``, so a Redis flush turns
"run now" into "run late" instead of "never run". All state (status, attempts,
last error, result) lives in the row; the Celery result backend stays disabled.

Handlers receive a plain ``dict`` payload (ids and enum values only) and are
registered with :func:`durable_task` or :func:`register_handler`. They run
inside ``with_clinic(clinic_id)`` whenever the job has a clinic, so tenant
managers work exactly as they do in a request.

Failure handling is deliberate:

* ``select_for_update`` claim with a status guard -> no double execution;
* ``attempts`` increments before the handler runs;
* exponential backoff (base 30 s, factor 2, +/-20% jitter) then ``pending``;
* ``failed`` once ``attempts`` reaches ``max_attempts``, ``dead`` for a job
  re-claimed after that;
* every failure on a tenant job writes an audit ``job.failed`` event.

Running the same idempotency key twice yields one row and one execution.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import record as record_audit
from .models import JobRequest
from .redaction import is_sensitive_key
from .rls import bypass_rls, clear_rls_context, current_rls_role, reset_rls_role, rls_context
from .tenancy import with_clinic

logger = logging.getLogger("enamel.jobs")

DEFAULT_QUEUE = "default"
TASK_QUEUES: tuple[str, ...] = ("default", "reminders", "ai", "ml", "sync")

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_DUE_BATCH_SIZE = 100
STALE_RUNNING_SECONDS = 600
BACKOFF_BASE_SECONDS = 30.0
BACKOFF_FACTOR = 2.0
BACKOFF_JITTER = 0.2
ERROR_MESSAGE_MAX_LENGTH = 500

# Payload keys that must never be persisted: credentials and tokens.
SECRET_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "card",
        "cookie",
        "credentials",
        "cvv",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "token",
    }
)


class JobError(RuntimeError):
    """Base class for job-layer failures."""


class JobPayloadInvalid(JobError):
    """The payload is not a JSON object, or contains secrets/PII."""


class JobKindUnknown(JobError):
    """No handler is registered for the job's kind."""


Handler = Callable[[dict[str, Any]], Any]

_HANDLERS: dict[str, Handler] = {}
_QUEUES: dict[str, str] = {}


def _setting(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


# --- registry --------------------------------------------------------------


def register_handler(kind: str, handler: Handler, *, queue: str = DEFAULT_QUEUE) -> Handler:
    """Register ``handler`` for ``kind`` (used by :func:`durable_task`)."""

    if not kind:
        raise ValueError("A job kind is required.")
    if queue not in TASK_QUEUES:
        raise ValueError(f"Unknown queue '{queue}'; expected one of {', '.join(TASK_QUEUES)}.")
    _HANDLERS[kind] = handler
    _QUEUES[kind] = queue
    return handler


def durable_task(kind: str, queue: str = DEFAULT_QUEUE) -> Callable[[Handler], Handler]:
    """Decorator registering a function as the handler for ``kind``."""

    def decorator(func: Handler) -> Handler:
        register_handler(kind, func, queue=queue)
        return func

    return decorator


def handler_for(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


def queue_for(kind: str) -> str:
    return _QUEUES.get(kind, DEFAULT_QUEUE)


def registered_kinds() -> tuple[str, ...]:
    return tuple(sorted(_HANDLERS))


# --- payload safety --------------------------------------------------------


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise JobPayloadInvalid("The job payload must be JSON-serialisable.") from exc


def _json_safe_or_none(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def _forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name.lower() in SECRET_PAYLOAD_KEYS or is_sensitive_key(name):
                found.append(f"{prefix}{name}")
            else:
                found.extend(_forbidden_keys(item, f"{prefix}{name}."))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_forbidden_keys(item, f"{prefix}{index}."))
    return found


def validate_payload(payload: Any) -> dict[str, Any]:
    """Return a JSON-safe copy of ``payload`` or raise ``JobPayloadInvalid``.

    Secret keys and PII keys (the same names the audit log redacts) are
    rejected rather than silently stripped: a handler that needs patient data
    must be passed a patient id and reload the record inside its clinic scope.
    """

    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise JobPayloadInvalid("The job payload must be a JSON object.")
    safe = _json_safe(payload)
    if not isinstance(safe, dict):
        raise JobPayloadInvalid("The job payload must be a JSON object.")
    forbidden = sorted(set(_forbidden_keys(safe)))
    if forbidden:
        raise JobPayloadInvalid(
            "The job payload must not contain secrets or PII: " + ", ".join(forbidden)
        )
    return safe


def _clinic_id(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    pk = getattr(value, "pk", None)
    if pk is not None and not isinstance(value, str):
        return pk
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# --- enqueue and dispatch --------------------------------------------------


def enqueue(
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    due_at: Any = None,
    idempotency_key: str | None = None,
    clinic: Any = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> JobRequest:
    """Write the durable intent row and dispatch the task on commit.

    ``idempotency_key`` makes the call safe to repeat (a retry returns the
    existing row and dispatches nothing). The dispatch is an
    ``on_commit`` callback so the task can never observe an uncommitted row.
    """

    if not kind or not isinstance(kind, str):
        raise JobPayloadInvalid("A job kind is required.")

    clinic_id = _clinic_id(clinic)
    safe_payload = validate_payload(payload)
    key = (idempotency_key or "").strip() or None
    attempts = int(max_attempts)
    if attempts < 1:
        raise JobPayloadInvalid("max_attempts must be at least 1.")

    job: JobRequest | None = None
    created = False
    due = due_at or timezone.now()
    with transaction.atomic():
        if key is not None:
            existing = JobRequest.objects.filter(
                clinic_id=clinic_id, idempotency_key=key
            ).first()
            if existing is not None:
                return existing

        try:
            # A savepoint keeps the surrounding transaction usable when the
            # unique constraint loses a race with a concurrent enqueue.
            with transaction.atomic():
                job = JobRequest.objects.create(
                    kind=kind,
                    payload=safe_payload,
                    idempotency_key=key,
                    clinic_id=clinic_id,
                    due_at=due,
                    max_attempts=attempts,
                )
            created = True
        except IntegrityError:
            job = JobRequest.objects.filter(
                clinic_id=clinic_id, idempotency_key=key
            ).first()
            if job is None:
                raise

        # A future-due job must not be handed to a worker now: the worker would
        # claim and execute it immediately, defeating the schedule. Beat's
        # ``scan_due_actions`` dispatches it once ``due_at`` arrives.
        if created and due <= timezone.now():
            transaction.on_commit(lambda job=job: dispatch_job(job))

    assert job is not None  # created or fetched above
    return job


def dispatch_job(job: JobRequest) -> None:
    """Send the job to its queue. A broker outage leaves the row pending."""

    try:
        run_job_task.apply_async(args=[str(job.id)], queue=queue_for(job.kind))
    except Exception:  # noqa: BLE001 - beat/scan will retry a pending row
        logger.warning(
            "job_dispatch_failed",
            extra={"job_id": str(job.id), "kind": job.kind, "outcome": "dispatch_failed"},
        )


# --- execution -------------------------------------------------------------


def _coerce_job_id(job_id: Any) -> uuid.UUID | None:
    if isinstance(job_id, uuid.UUID):
        return job_id
    try:
        return uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return None


def claim_job(job_id: Any) -> JobRequest | None:
    """Claim a pending row, or return None when there is nothing to run.

    Returns None for a missing row, a finished row, an already-running row (the
    stale path owns recovery) and a row past ``max_attempts`` (which is moved
    to ``dead``).
    """

    resolved = _coerce_job_id(job_id)
    if resolved is None:
        return None

    with transaction.atomic(), bypass_rls():
        job = JobRequest.objects.select_for_update().filter(pk=resolved).first()
        if job is None:
            return None
        if job.status in (
            JobRequest.Status.DONE,
            JobRequest.Status.RUNNING,
            JobRequest.Status.DEAD,
        ):
            return None
        if job.attempts >= job.max_attempts:
            job.status = JobRequest.Status.DEAD
            job.locked_at = None
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "locked_at", "finished_at", "updated_at"])
            return None
        # A first attempt may only run when the row is due. Retries are exempt:
        # their ``due_at`` is the backoff target and keeping that guard for them
        # would strand a retry that a worker picked up early. ``run_job`` is
        # called directly by the eager path, so this guard is the last line of
        # defence against early execution.
        if job.attempts == 0 and job.due_at and job.due_at > timezone.now():
            return None

        job.status = JobRequest.Status.RUNNING
        job.locked_at = timezone.now()
        job.attempts += 1
        job.save(update_fields=["status", "locked_at", "attempts", "updated_at"])
        return job


def compute_backoff(attempts: int) -> timedelta:
    """Exponential backoff with jitter: ``base * factor**(n-1)`` +/-20%."""

    base = float(_setting("JOB_BACKOFF_BASE_SECONDS", BACKOFF_BASE_SECONDS))
    factor = float(_setting("JOB_BACKOFF_FACTOR", BACKOFF_FACTOR))
    jitter = float(_setting("JOB_BACKOFF_JITTER", BACKOFF_JITTER))
    delay = base * (factor ** max(0, int(attempts) - 1))
    spread = delay * jitter
    delay += random.uniform(-spread, spread)
    return timedelta(seconds=max(0.0, delay))


def _error_payload(exc: BaseException) -> dict[str, str]:
    message = " ".join(str(exc).split())[:ERROR_MESSAGE_MAX_LENGTH]
    return {"type": type(exc).__name__, "message": message}


def _payload_uuid(payload: Any, *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        raw = payload.get(key)
        if raw:
            try:
                return str(uuid.UUID(str(raw)))
            except (TypeError, ValueError):
                continue
    return None


@contextmanager
def _scope_for(job: JobRequest) -> Iterator[None]:
    """Activate the job's clinic in both the ORM scope and the database GUC.

    ``app.current_clinic_id`` is what makes RLS apply inside the handler;
    ``app.current_user_id`` is informational and comes from the payload when
    the enqueuing service recorded an actor.

    Every task starts from a clean slate and leaves none behind (T137): the
    context GUCs - including the portal ones - are cleared and the database
    role is reset, so a task can never inherit the context of the task before
    it.
    """

    previous_role = current_rls_role()
    clear_rls_context()
    try:
        if not job.clinic_id:
            yield
            return

        user_id = _payload_uuid(job.payload, "actor_id", "user_id")
        with with_clinic(job.clinic_id), rls_context(job.clinic_id, user_id or ""):
            yield
    finally:
        clear_rls_context()
        reset_rls_role(previous_role)


def _record_success(job: JobRequest, result: Any) -> JobRequest:
    with transaction.atomic(), bypass_rls():
        locked = JobRequest.objects.select_for_update().get(pk=job.pk)
        locked.status = JobRequest.Status.DONE
        locked.result = _json_safe_or_none(result)
        locked.last_error = None
        locked.locked_at = None
        locked.finished_at = timezone.now()
        locked.save(
            update_fields=[
                "status",
                "result",
                "last_error",
                "locked_at",
                "finished_at",
                "updated_at",
            ]
        )
    logger.info(
        "job_done",
        extra={"job_id": str(locked.pk), "kind": locked.kind, "outcome": "done"},
    )
    return locked


def _record_failure(job: JobRequest, exc: BaseException) -> JobRequest:
    now = timezone.now()
    error = _error_payload(exc)

    with transaction.atomic(), bypass_rls():
        locked = JobRequest.objects.select_for_update().get(pk=job.pk)
        locked.last_error = error
        locked.locked_at = None
        if locked.attempts >= locked.max_attempts:
            locked.status = JobRequest.Status.FAILED
            locked.finished_at = now
        else:
            locked.status = JobRequest.Status.PENDING
            locked.due_at = now + compute_backoff(locked.attempts)
        locked.save(
            update_fields=[
                "status",
                "locked_at",
                "last_error",
                "due_at",
                "finished_at",
                "updated_at",
            ]
        )

        if locked.clinic_id:
            record_audit(
                "job.failed",
                entity=locked,
                clinic=locked.clinic_id,
                after={
                    "kind": locked.kind,
                    "attempts": locked.attempts,
                    "max_attempts": locked.max_attempts,
                    "status": locked.status,
                    "error_type": error["type"],
                },
            )

    logger.warning(
        "job_failed",
        extra={
            "job_id": str(locked.pk),
            "kind": locked.kind,
            "attempts": locked.attempts,
            "outcome": locked.status,
        },
    )
    return locked


def run_job(job_id: Any) -> JobRequest | None:
    """Claim and execute one job. This is the single execution code path.

    Called directly by tests and by ``scan_due_actions``; the Celery entry
    point :func:`run_job_task` is a thin wrapper around it so eager and worker
    execution cannot diverge.
    """

    job = claim_job(job_id)
    if job is None:
        return None

    handler = handler_for(job.kind)
    if handler is None:
        return _record_failure(job, JobKindUnknown(f"No handler for kind '{job.kind}'."))

    try:
        with _scope_for(job):
            result = handler(dict(job.payload or {}))
    except Exception as exc:  # noqa: BLE001 - any handler failure is a job failure
        return _record_failure(job, exc)

    return _record_success(job, result)


@shared_task(name="core.jobs.run_job")
def run_job_task(job_id: str) -> str | None:
    """Celery entry point: same code path as calling :func:`run_job`."""

    run_job(job_id)
    return str(job_id)


# --- scheduling helpers ----------------------------------------------------


def due_jobs(limit: int | None = None) -> list[JobRequest]:
    """Pending jobs whose ``due_at`` has arrived, oldest first."""

    batch = int(_setting("JOB_DUE_BATCH_SIZE", DEFAULT_DUE_BATCH_SIZE)) if limit is None else int(limit)
    # Platform scheduling is cross-clinic by definition, so it runs under the
    # explicit maintenance bypass. The handler itself runs with one clinic.
    with bypass_rls():
        return list(
            JobRequest.objects.filter(
                status=JobRequest.Status.PENDING,
                due_at__lte=timezone.now(),
            ).order_by("due_at", "created_at")[:batch]
        )


def due_job_ids(limit: int | None = None) -> list[uuid.UUID]:
    return [job.id for job in due_jobs(limit=limit)]


def dispatch_due_jobs(limit: int | None = None) -> int:
    """Dispatch every due job through the shared ``run_job`` path."""

    dispatched = 0
    for job in due_jobs(limit=limit):
        run_job_task.apply_async(args=[str(job.id)], queue=queue_for(job.kind))
        dispatched += 1
    return dispatched


def reclaim_stale_jobs(older_than_seconds: float | None = None) -> int:
    """Return jobs stuck in ``running`` to the queue.

    A worker killed mid-job leaves ``running`` + ``locked_at``. Anything locked
    longer than the stale window becomes ``pending`` again (or ``dead`` when its
    attempts are exhausted), which is what makes a crash a retry and not a lost
    job.
    """

    if older_than_seconds is None:
        older_than_seconds = float(
            _setting("JOB_STALE_RUNNING_SECONDS", STALE_RUNNING_SECONDS)
        )
    now = timezone.now()
    cutoff = now - timedelta(seconds=float(older_than_seconds))

    reclaimed = 0
    with transaction.atomic(), bypass_rls():
        stale = list(
            JobRequest.objects.select_for_update(skip_locked=True).filter(
                status=JobRequest.Status.RUNNING,
                locked_at__lt=cutoff,
            )
        )
        for job in stale:
            if job.attempts >= job.max_attempts:
                job.status = JobRequest.Status.DEAD
                job.finished_at = now
            else:
                job.status = JobRequest.Status.PENDING
                job.due_at = now
            job.locked_at = None
            job.save(
                update_fields=[
                    "status",
                    "locked_at",
                    "due_at",
                    "finished_at",
                    "updated_at",
                ]
            )
            reclaimed += 1
    return reclaimed


def cleanup_jobs(
    *,
    done_retention_days: int | None = None,
    failed_retention_days: int | None = None,
) -> dict[str, int]:
    """Delete finished job rows past their retention window."""

    done_days = (
        int(_setting("JOB_DONE_RETENTION_DAYS", 7))
        if done_retention_days is None
        else int(done_retention_days)
    )
    failed_days = (
        int(_setting("JOB_FAILED_RETENTION_DAYS", 30))
        if failed_retention_days is None
        else int(failed_retention_days)
    )
    now = timezone.now()

    with bypass_rls():
        done_deleted = JobRequest.objects.filter(
            status=JobRequest.Status.DONE,
            finished_at__lt=now - timedelta(days=done_days),
        ).delete()[0]
        failed_deleted = JobRequest.objects.filter(
            status__in=[JobRequest.Status.FAILED, JobRequest.Status.DEAD],
            finished_at__lt=now - timedelta(days=failed_days),
        ).delete()[0]

    return {"done_jobs": done_deleted, "failed_jobs": failed_deleted}


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_FACTOR",
    "BACKOFF_JITTER",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_QUEUE",
    "JobError",
    "JobKindUnknown",
    "JobPayloadInvalid",
    "TASK_QUEUES",
    "claim_job",
    "cleanup_jobs",
    "compute_backoff",
    "dispatch_due_jobs",
    "dispatch_job",
    "due_job_ids",
    "due_jobs",
    "durable_task",
    "enqueue",
    "handler_for",
    "queue_for",
    "reclaim_stale_jobs",
    "register_handler",
    "registered_kinds",
    "run_job",
    "run_job_task",
    "validate_payload",
]
