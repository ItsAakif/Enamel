"""Voice flow tests (T124): start, identity, booking, ASR, consent."""

from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from apps.ai.models import VoiceCall, VoiceTurn
from apps.scheduling.models import Appointment

CALLS_URL = "/api/v1/ai/calls/"


def _start(owner, payload=None, **extra):
    response = owner.post(CALLS_URL, payload or extra)
    assert response.status_code == 201, response.content
    return response.json()


def _turn(owner, call_id, **payload):
    response = owner.post(f"{CALLS_URL}{call_id}/turns/", payload)
    assert response.status_code == 200, response.content
    return response.json()


def _tool(owner, call_id, tool, **payload):
    return owner.post(f"{CALLS_URL}{call_id}/tools/{tool}/", payload)


def test_start_call_creates_greeting_and_simulator_fields(owner, ai_seed):
    seed = ai_seed()
    body = _start(owner, {"patient_id": str(seed["patient"].id), "recording_consent": False})
    assert body["mode"] == "local_test"
    assert body["adapter"] == "local_test"
    assert body["status"] == "active"
    assert body["recording_consent"] is False
    assert body["recording_key"] is None
    assert body["trace_id"]
    assert [turn["role"] for turn in body["turns"]] == ["assistant"]
    assert body["turns"][0]["text"]


def test_recording_consent_false_stores_no_key(owner, ai_seed):
    seed = ai_seed()
    body = _start(owner, {"patient_id": str(seed["patient"].id), "recording_consent": False})
    call = VoiceCall.all_objects.get(pk=body["id"])
    assert call.recording_key == ""
    assert call.recording_consent is False


def test_recording_consent_true_sets_key(owner, ai_seed):
    ai_seed()
    body = _start(owner, {"recording_consent": True})
    assert body["recording_key"]
    assert body["recording_consent"] is True


def test_identity_gate_before_patient_specific_tools(owner, ai_seed):
    seed = ai_seed(date_of_birth=date(1990, 1, 1))
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    cid = call["id"]
    start = (timezone.now() + timedelta(days=3)).isoformat()
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start}

    blocked = _tool(owner, cid, "book_appointment", args=args)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "identity_required"

    confirmed = _turn(owner, cid, text="my date of birth is 1990-01-01")
    assert confirmed["intent"] == "identity"
    detail = owner.get(f"{CALLS_URL}{cid}/").json()
    assert detail["identity_confirmed"] is True


def test_booking_flow_with_confirmation(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    cid = call["id"]
    _turn(owner, cid, text="my date of birth is 1990-01-01")

    start = (timezone.now() + timedelta(days=3)).isoformat()
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start}

    first = _tool(owner, cid, "book_appointment", args=args)
    assert first.status_code == 409
    assert first.json()["error"]["code"] == "confirmation_required"
    nonce = first.json()["error"]["details"]["confirmation_nonce"]

    booked = _tool(
        owner,
        cid,
        "book_appointment",
        args=args,
        confirmed=True,
        confirmation_nonce=nonce,
    )
    assert booked.status_code == 200, booked.content
    assert booked.json()["result"]["duplicate"] is False
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 1

    detail = owner.get(f"{CALLS_URL}{cid}/").json()
    assert detail["outcome"] == "booked"
    assert detail["summary"]
    assert detail["trace_id"]
    assert detail["latency_ms"] >= 0
    assert detail["cost_minor"] >= 0
    assert len(detail["tool_calls"]) >= 1


def test_asr_low_confidence_repeats_then_escalates(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    cid = call["id"]

    first = _turn(owner, cid, text="mumble", confidence=0.2)
    assert first["escalated"] is False
    second = _turn(owner, cid, text="mumble", confidence=0.2)
    assert second["escalated"] is False
    third = _turn(owner, cid, text="mumble", confidence=0.2)
    assert third["escalated"] is True

    detail = owner.get(f"{CALLS_URL}{cid}/").json()
    assert detail["status"] == "escalated"
    assert detail["ended_at"] is not None


def test_turns_after_escalation_are_ended(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    cid = call["id"]
    owner.post(f"{CALLS_URL}{cid}/escalate/", {}, format="json")
    ended = _turn(owner, cid, text="hello?")
    assert ended["escalated"] is True


def test_call_list_and_detail_are_tenant_scoped(owner, staff_factory, ai_seed):
    seed = ai_seed()
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    other = staff_factory(role="owner")

    listing = owner.get(CALLS_URL)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    assert other.get(f"{CALLS_URL}{call['id']}/").status_code == 404


def test_turn_persists_caller_and_assistant_turns(owner, ai_seed):
    seed = ai_seed()
    call = _start(owner, {"patient_id": str(seed["patient"].id)})
    cid = call["id"]
    _turn(owner, cid, text="what are your opening hours?")
    turns = VoiceTurn.all_objects.filter(call_id=cid).order_by("ordinal")
    roles = [turn.role for turn in turns]
    assert roles.count("assistant") >= 2
    assert "caller" in roles
