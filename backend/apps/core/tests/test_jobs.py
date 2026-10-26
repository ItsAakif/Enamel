"""T104 durable job tests: enqueue, claim, retry, dead-letter, beat, scope.

Coverage:

1. enqueue writes ``pending``; running marks ``running`` then ``done``;
2. the same idempotency key yields one row and one execution;
3. a failing handler retries with backoff, then ``failed`` + audit;
4. a killed worker leaves ``running`` and the reclaim path re-queues it;
5. the beat schedule contains every required entry (including the T112
   ``expire_holds`` hold sweeper);
6. eager mode runs the same ``run_job`` code path as production.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core import jobs
from apps.core.jobs import (
    JobPayloadInvalid,
    compute_backoff,
    dispatch_due_jobs,
    enqueue,
    handler_for,
    queue_for,
    reclaim_stale_jobs,
    register_handler,
    run_job,
)
from apps.core.models import AuditEvent, IdempotencyRecord, JobRequest
from apps.core.tasks import cleanup_expired, heartbeat, reclaim_stale, scan_due_actions

pytestmark = pytest.mark.django_db


def unique_kind(name: str = "kind") -> str:
    return f"test.jobs.{name}.{uuid.uuid4().hex[:8]}"


def boom(payload):
    raise RuntimeError("handler exploded")


# --- happy path ------------------------------------------------------------


def test_enqueue_is_pending_and_run_marks_done():
    calls: list[dict] = []
    kind = unique_kind("ok")
    register_handler(kind, lambda payload: calls.append(payload) or {"ok": True})

    job = enqueue(kind, {"action_id": "abc"})

    assert job.status == JobRequest.Status.PENDING
    assert job.attempts == 0
    assert job.due_at is not None
    assert JobRequest.objects.filter(pk=job.id).count() == 1

    result = run_job(job.id)

    assert result is not None
    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert job.result == {"ok": True}
    assert job.attempts == 1
    assert job.locked_at is None
    assert job.finished_at is not None
    assert calls == [{"action_id": "abc"}]
    assert handler_for(kind) is not None


def test_run_job_is_a_no_op_once_done():
    calls: list[dict] = []
    kind = unique_kind("idempotent-run")
    register_handler(kind, lambda payload: calls.append(payload) or "ok")
    job = enqueue(kind, {})

    run_job(job.id)
    run_job(job.id)
    run_job(job.id)

    assert len(calls) == 1
    job.refresh_from_db()
    assert job.attempts == 1
    assert job.status == JobRequest.Status.DONE


def test_missing_kind_registration_fails_the_job():
    job = enqueue(unique_kind("no-handler"), {})

    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING  # retryable
    assert job.last_error["type"] == "JobKindUnknown"


# --- idempotency -----------------------------------------------------------


def test_same_idempotency_key_creates_one_row_and_one_execution():
    calls: list[dict] = []
    kind = unique_kind("idempotency")
    register_handler(kind, lambda payload: calls.append(payload) or "ok")

    first = enqueue(kind, {"a": 1}, idempotency_key="nightly-sync")
    second = enqueue(kind, {"a": 1}, idempotency_key="nightly-sync")

    assert first.id == second.id
    assert JobRequest.objects.filter(kind=kind).count() == 1

    run_job(first.id)
    run_job(second.id)

    assert len(calls) == 1


def test_idempotency_key_is_scoped_per_clinic(clinic_factory):
    first_clinic = clinic_factory(slug="idem-a")
    second_clinic = clinic_factory(slug="idem-b")
    kind = unique_kind("idem-scope")
    register_handler(kind, lambda payload: "ok")

    first = enqueue(kind, {}, idempotency_key="shared", clinic=first_clinic)
    second = enqueue(kind, {}, idempotency_key="shared", clinic=second_clinic)

    assert first.id != second.id
    assert JobRequest.objects.filter(kind=kind).count() == 2


def test_null_idempotency_keys_do_not_collide():
    kind = unique_kind("null-keys")
    register_handler(kind, lambda payload: "ok")

    first = enqueue(kind, {})
    second = enqueue(kind, {})

    assert first.id != second.id
    assert JobRequest.objects.filter(kind=kind).count() == 2


# --- payload safety --------------------------------------------------------


def test_payload_rejects_secrets_and_pii():
    kind = unique_kind("payload")

    with pytest.raises(JobPayloadInvalid):
        enqueue(kind, {"password": "hunter2"})
    with pytest.raises(JobPayloadInvalid):
        enqueue(kind, {"provider": {"api_key": "sk-live"}})
    with pytest.raises(JobPayloadInvalid):
        enqueue(kind, {"patient": {"notes": "free text"}})
    with pytest.raises(JobPayloadInvalid):
        enqueue(kind, ["not", "an", "object"])

    assert JobRequest.objects.filter(kind=kind).count() == 0

    job = enqueue(kind, {"patient_id": str(uuid.uuid4()), "count": 3, "mode": "reminder"})
    assert job.payload["count"] == 3
    assert job.payload["mode"] == "reminder"


def test_last_error_carries_type_and_message_only(clinic_factory):
    clinic = clinic_factory(slug="error-shape")
    kind = unique_kind("error-shape")
    register_handler(kind, boom)

    job = enqueue(kind, {"patient_id": str(uuid.uuid4())}, clinic=clinic)
    run_job(job.id)

    job.refresh_from_db()
    assert set(job.last_error) == {"type", "message"}
    assert job.last_error == {"type": "RuntimeError", "message": "handler exploded"}


# --- retries and dead-letter ----------------------------------------------


def test_backoff_is_exponential_within_jitter_bounds():
    expected_base = {1: 30.0, 2: 60.0, 3: 120.0, 4: 240.0}

    for attempts, base in expected_base.items():
        samples = [compute_backoff(attempts).total_seconds() for _ in range(60)]
        assert all(base * 0.8 <= value <= base * 1.2 for value in samples)
        assert len(set(samples)) > 1  # jitter actually varies the delay

    # A zero/negative attempt count is treated as the first attempt.
    assert compute_backoff(0).total_seconds() > 0


def test_failure_retries_with_incremented_attempts_and_backoff():
    kind = unique_kind("retry")
    register_handler(kind, boom)
    job = enqueue(kind, {}, max_attempts=5)

    started = timezone.now()
    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.attempts == 1
    assert job.locked_at is None
    first_delay = (job.due_at - started).total_seconds()
    assert 24.0 <= first_delay <= 36.5

    run_job(job.id)

    job.refresh_from_db()
    assert job.attempts == 2
    assert job.status == JobRequest.Status.PENDING
    second_delay = (job.due_at - started).total_seconds()
    assert 48.0 <= second_delay <= 73.0
    assert second_delay > first_delay


def test_exhausted_job_fails_records_an_audit_and_then_dies(clinic_factory):
    clinic = clinic_factory(slug="job-failed-audit")
    kind = unique_kind("exhausted")
    register_handler(kind, boom)
    job = enqueue(kind, {}, clinic=clinic, max_attempts=2)

    run_job(job.id)
    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING

    run_job(job.id)
    job.refresh_from_db()
    assert job.status == JobRequest.Status.FAILED
    assert job.attempts == 2
    assert job.finished_at is not None

    events = AuditEvent.objects.filter(clinic=clinic, action="job.failed").order_by("id")
    assert events.count() == 2
    last = events.last()
    assert last.entity_type == "core.jobrequest"
    assert last.entity_id == str(job.id)
    assert last.after["status"] == "failed"
    assert last.after["kind"] == kind
    assert last.after["error_type"] == "RuntimeError"

    # A job claimed after max_attempts is dead-lettered, not re-run.
    run_job(job.id)
    job.refresh_from_db()
    assert job.status == JobRequest.Status.DEAD


def test_failure_without_a_clinic_writes_no_audit():
    kind = unique_kind("platform-failure")
    register_handler(kind, boom)
    job = enqueue(kind, {}, max_attempts=1)

    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.FAILED
    assert AuditEvent.objects.filter(action="job.failed").count() == 0


# --- stale reclamation -----------------------------------------------------


def test_stale_running_job_is_reclaimed_and_finished_by_the_scan():
    calls: list[dict] = []
    kind = unique_kind("stale")
    register_handler(kind, lambda payload: calls.append(payload) or "ok")
    job = enqueue(kind, {})

    claimed = jobs.claim_job(job.id)
    assert claimed is not None
    assert claimed.status == JobRequest.Status.RUNNING
    assert claimed.attempts == 1

    # Simulate a worker killed after the claim: the row stays running and the
    # clock moves past the stale window.
    JobRequest.objects.filter(pk=job.id).update(
        locked_at=timezone.now() - timedelta(minutes=20)
    )

    assert reclaim_stale_jobs() == 1
    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.locked_at is None
    assert job.attempts == 1  # the attempt is not refunded

    scan_due_actions()

    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert job.attempts == 2
    assert len(calls) == 1


def test_fresh_running_job_is_not_reclaimed():
    kind = unique_kind("fresh")
    register_handler(kind, lambda payload: "ok")
    job = enqueue(kind, {})
    jobs.claim_job(job.id)

    assert reclaim_stale_jobs() == 0

    job.refresh_from_db()
    assert job.status == JobRequest.Status.RUNNING


def test_reclaim_task_wrapper_returns_a_count():
    kind = unique_kind("reclaim-task")
    register_handler(kind, lambda payload: "ok")
    job = enqueue(kind, {})
    jobs.claim_job(job.id)
    JobRequest.objects.filter(pk=job.id).update(
        locked_at=timezone.now() - timedelta(minutes=30)
    )

    assert reclaim_stale() == {"reclaimed": 1}


# --- scope -----------------------------------------------------------------


def test_tenant_job_runs_inside_with_clinic(clinic_factory):
    clinic = clinic_factory(slug="scope-ok")
    seen: dict = {}
    kind = unique_kind("scope-ok")

    def handler(payload):
        from apps.core.tenancy import current_scope

        scope = current_scope()
        seen["clinic_id"] = scope.clinic_id if scope else None
        return "ok"

    register_handler(kind, handler)
    job = enqueue(kind, {}, clinic=clinic)

    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert seen["clinic_id"] == clinic.id


def test_tenant_handler_without_a_clinic_fails_with_unscoped_query():
    kind = unique_kind("scope-missing")

    def handler(payload):
        from apps.clinics.models import Location

        return Location.objects.count()

    register_handler(kind, handler)
    job = enqueue(kind, {})  # no clinic

    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.last_error["type"] == "UnscopedQueryError"


def test_tenant_handler_with_a_clinic_can_use_the_tenant_manager(clinic_factory):
    clinic = clinic_factory(slug="scope-manager")
    kind = unique_kind("scope-manager")

    def handler(payload):
        from apps.clinics.models import Location

        return Location.objects.count()

    register_handler(kind, handler)
    job = enqueue(kind, {}, clinic=clinic)

    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert job.result == 0


# --- scheduling, queues, beat ---------------------------------------------


def test_underlying_handlers_are_plain_ids_only():
    kind = unique_kind("ids")
    payloads: list[dict] = []
    register_handler(kind, lambda payload: payloads.append(payload) or None)

    job = enqueue(kind, {"appointment_id": str(uuid.uuid4()), "kind": "reminder"})
    run_job(job.id)

    assert payloads == [job.payload]
    assert all(isinstance(value, (str, int, float, bool, type(None))) for value in payloads[0].values())


def test_kind_queue_routing_and_validation():
    kind = unique_kind("queue")
    register_handler(kind, lambda payload: None, queue="reminders")

    assert queue_for(kind) == "reminders"
    assert queue_for("unregistered.kind") == jobs.DEFAULT_QUEUE
    assert jobs.DEFAULT_QUEUE == "default"

    with pytest.raises(ValueError):
        register_handler(unique_kind("badqueue"), lambda payload: None, queue="not-a-queue")
    with pytest.raises(ValueError):
        register_handler("", lambda payload: None)


def test_registered_queues_match_the_declared_task_queues():
    from config.celery import TASK_QUEUES

    assert set(TASK_QUEUES) == set(jobs.TASK_QUEUES)
    assert "default" in TASK_QUEUES


def test_beat_schedule_contains_the_required_entries():
    from config.celery import app

    schedule = app.conf.beat_schedule
    # The schedule is a superset of the required entries: T112 adds
    # ``expire_holds`` and later tasks may add more, but these five must always
    # be present so the durable-job and hold machinery keeps running.
    required = {
        "scan_due_actions",
        "cleanup_expired",
        "heartbeat",
        "reclaim_stale",
        "expire_holds",
    }
    assert required <= set(schedule)
    assert schedule["scan_due_actions"]["task"] == "core.tasks.scan_due_actions"
    assert schedule["scan_due_actions"]["schedule"] == 60.0
    assert schedule["cleanup_expired"]["task"] == "core.tasks.cleanup_expired"
    assert schedule["heartbeat"]["task"] == "core.tasks.heartbeat"
    assert schedule["reclaim_stale"]["task"] == "core.tasks.reclaim_stale"
    assert schedule["expire_holds"]["task"] == "booking.tasks.expire_holds"
    assert schedule["expire_holds"]["schedule"] == 60.0


def test_eager_mode_uses_the_single_run_job_code_path(
    monkeypatch, django_capture_on_commit_callbacks
):
    calls: list[str] = []
    kind = unique_kind("eager")
    register_handler(kind, lambda payload: "ok")

    real_run_job = jobs.run_job

    def spy(job_id):
        calls.append(str(job_id))
        return real_run_job(job_id)

    monkeypatch.setattr(jobs, "run_job", spy)

    with django_capture_on_commit_callbacks(execute=True):
        job = enqueue(kind, {})

    assert calls == [str(job.id)]
    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert jobs.run_job_task.name == "core.jobs.run_job"


def test_scan_due_actions_dispatches_through_run_job(monkeypatch):
    calls: list[str] = []
    kind = unique_kind("scan")
    register_handler(kind, lambda payload: "ok")
    job = enqueue(kind, {}, due_at=timezone.now() - timedelta(seconds=5))

    real_run_job = jobs.run_job

    def spy(job_id):
        calls.append(str(job_id))
        return real_run_job(job_id)

    monkeypatch.setattr(jobs, "run_job", spy)

    result = scan_due_actions()

    assert result["dispatched"] == 1
    assert calls == [str(job.id)]
    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE


def test_future_jobs_are_not_dispatched():
    kind = unique_kind("future")
    register_handler(kind, lambda payload: "ok")
    enqueue(kind, {}, due_at=timezone.now() + timedelta(hours=1))

    assert dispatch_due_jobs() == 0


def test_future_due_job_is_not_dispatched_on_commit(
    monkeypatch, django_capture_on_commit_callbacks
):
    calls: list[str] = []
    kind = unique_kind("future-on-commit")
    register_handler(kind, lambda payload: "ok")

    real_run_job = jobs.run_job

    def spy(job_id):
        calls.append(str(job_id))
        return real_run_job(job_id)

    monkeypatch.setattr(jobs, "run_job", spy)

    with django_capture_on_commit_callbacks(execute=True):
        job = enqueue(kind, {}, due_at=timezone.now() + timedelta(hours=1))

    assert calls == []
    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.attempts == 0


def test_claim_job_refuses_a_future_due_first_attempt():
    kind = unique_kind("future-claim")
    register_handler(kind, lambda payload: {"ok": True})
    job = enqueue(kind, {}, due_at=timezone.now() + timedelta(hours=1))

    assert run_job(job.id) is None
    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.attempts == 0

    JobRequest.objects.filter(pk=job.id).update(due_at=timezone.now() - timedelta(seconds=1))
    result = run_job(job.id)

    assert result is not None
    job.refresh_from_db()
    assert job.status == JobRequest.Status.DONE
    assert job.attempts == 1


def test_retry_with_future_backoff_is_still_claimable():
    kind = unique_kind("retry-claim")
    register_handler(kind, boom)
    job = enqueue(kind, {})
    run_job(job.id)

    job.refresh_from_db()
    assert job.status == JobRequest.Status.PENDING
    assert job.attempts == 1
    assert job.due_at > timezone.now()

    # A retry row carries its backoff target in ``due_at``. The guard is only
    # for a first attempt, so a worker that picks it up can still run it.
    assert run_job(job.id) is not None
    job.refresh_from_db()
    assert job.attempts == 2


# --- housekeeping ----------------------------------------------------------


def test_cleanup_expired_purges_old_done_jobs_and_idempotency_records(
    user_factory, clinic_factory
):
    kind = unique_kind("cleanup")
    register_handler(kind, lambda payload: "ok")
    done_job = enqueue(kind, {})
    run_job(done_job.id)
    JobRequest.objects.filter(pk=done_job.id).update(
        finished_at=timezone.now() - timedelta(days=40)
    )

    user = user_factory("cleanup@jobs.test")
    clinic = clinic_factory(slug="cleanup-clinic")
    IdempotencyRecord.objects.create(
        user=user,
        clinic=clinic,
        key="stale",
        method="POST",
        path="/api/v1/clinics/",
        request_hash="0" * 64,
        status_code=201,
        expires_at=timezone.now() - timedelta(hours=1),
    )

    result = cleanup_expired()

    assert result["done_jobs"] == 1
    assert result["idempotency_records"] == 1
    assert not JobRequest.objects.filter(pk=done_job.id).exists()
    assert IdempotencyRecord.objects.count() == 0


def test_cleanup_keeps_recent_finished_jobs():
    kind = unique_kind("cleanup-recent")
    register_handler(kind, lambda payload: "ok")
    job = enqueue(kind, {})
    run_job(job.id)

    result = cleanup_expired()

    assert result["done_jobs"] == 0
    assert JobRequest.objects.filter(pk=job.id).exists()


def test_heartbeat_reports_ok():
    result = heartbeat()

    assert result["status"] == "ok"
    assert result["service"] == "enamel-worker"
