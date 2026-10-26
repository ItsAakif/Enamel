"""Escalation tests (T124): durable staff task, rollback, log hygiene."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone

from apps.ai.tools.base import ToolContext
from apps.ai.tools.registry import get_registry
from apps.core.models import JobRequest
from apps.scheduling.models import Appointment

CALLS_URL = "/api/v1/ai/calls/"


def _start(owner, patient_id):
    response = owner.post(CALLS_URL, {"patient_id": str(patient_id)})
    assert response.status_code == 201, response.content
    return response.json()


def _ctx(owner, call, patient):
    return ToolContext(
        clinic=owner.clinic,
        user=owner.user,
        membership=owner.membership,
        call=call,
        patient=patient,
    )


def test_escalate_endpoint_creates_job_and_ends_call(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, seed["patient"].id)
    response = owner.post(
        f"{CALLS_URL}{call['id']}/escalate/", {"reason_code": "caller_request"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "escalated"
    assert JobRequest.objects.filter(kind="ai.escalation").count() == 1

    detail = owner.get(f"{CALLS_URL}{call['id']}/").json()
    assert detail["status"] == "escalated"
    assert detail["ended_at"] is not None


def test_escalate_tool_via_api(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, seed["patient"].id)
    response = owner.post(
        f"{CALLS_URL}{call['id']}/tools/escalate_to_human/",
        {"args": {"reason_code": "caller_request"}},
        format="json",
    )
    assert response.status_code == 200, response.content
    assert response.json()["status"] == "ok"
    assert JobRequest.objects.filter(kind="ai.escalation").exists()


def test_tool_failure_rolls_back_and_escalates(owner, ai_seed, seed_call, monkeypatch):
    seed = ai_seed()
    call = seed_call(owner, seed["patient"])
    start = timezone.now() + timedelta(days=3)
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()}
    ctx = _ctx(owner, call, seed["patient"])

    first = get_registry().execute(ctx, "book_appointment", args)
    nonce = first.details["confirmation_nonce"]

    from apps.ai.tools import booking as booking_module

    real = booking_module.create_appointment

    def boom(*call_args, **call_kwargs):
        real(*call_args, **call_kwargs)
        raise IntegrityError("simulated constraint failure")

    monkeypatch.setattr(booking_module, "create_appointment", boom)

    result = get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    )
    assert result.status == "error"
    assert result.error_code == "tool_error"
    # The partial write rolled back: no appointment remains.
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 0
    # A durable staff task was created and the session ended.
    assert JobRequest.objects.filter(kind="ai.escalation").exists()
    call.refresh_from_db()
    assert call.status == "escalated"


def test_no_phi_in_logs(owner, ai_seed, patient_factory, caplog):
    ai_seed()
    secret = patient_factory(
        owner.clinic,
        first_name="Zelda",
        last_name="Fitzgerald-Quint",
        phone_e164="+919876500123",
    )
    caplog.set_level(logging.DEBUG)
    call = _start(owner, secret.id)
    owner.post(
        f"{CALLS_URL}{call['id']}/turns/",
        {"text": "my name is Zelda Fitzgerald-Quint"},
        format="json",
    )
    owner.post(f"{CALLS_URL}{call['id']}/escalate/", {}, format="json")

    assert "Zelda" not in caplog.text
    assert "Fitzgerald-Quint" not in caplog.text
    assert "+919876500123" not in caplog.text


def test_escalation_payload_has_no_phi(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, seed["patient"].id)
    owner.post(f"{CALLS_URL}{call['id']}/escalate/", {}, format="json")
    job = JobRequest.objects.get(kind="ai.escalation")
    blob = str(job.payload)
    assert "Ada" not in blob
    assert "Lovelace" not in blob
    assert "+919876500000" not in blob
    assert set(job.payload) == {"reason_code", "call_id"}
