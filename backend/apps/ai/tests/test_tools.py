"""Tool registry tests (T124): rejection gates, confirmation, idempotency."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ai.models import ToolInvocation
from apps.ai.providers.mock import MockProvider
from apps.ai.tools.base import ToolContext
from apps.ai.tools.registry import get_registry
from apps.ai.voice.engine import ConversationEngine
from apps.core.models import JobRequest
from apps.scheduling.models import Appointment


@pytest.fixture
def engine():
    return ConversationEngine(provider=MockProvider())


def _ctx(owner, *, call=None, patient=None, confirmed=False, nonce="", membership=None):
    return ToolContext(
        clinic=owner.clinic,
        user=owner.user,
        membership=membership or owner.membership,
        call=call,
        patient=patient,
        confirmed=confirmed,
        confirmation_nonce=nonce,
    )


def _call(engine, owner, patient, *, confirmed=True):
    call = engine.start_call(clinic=owner.clinic, patient=patient, started_by=owner.user)
    if confirmed:
        call.identity_confirmed = True
        call.save(update_fields=["identity_confirmed", "updated_at"])
    return call


def test_registry_names_and_specs():
    registry = get_registry()
    assert registry.names() == [
        "book_appointment",
        "cancel_appointment",
        "check_availability",
        "escalate_to_human",
        "lookup_faq",
        "reschedule_appointment",
    ]
    specs = registry.openai_tools()
    assert all(spec["type"] == "function" for spec in specs)


def test_unknown_tool_is_rejected(owner, ai_seed):
    ai_seed()
    result = get_registry().execute(_ctx(owner), "delete_everything", {})
    assert result.status == "rejected"
    assert result.error_code == "unknown_tool"
    assert ToolInvocation.all_objects.filter(tool="delete_everything").exists()


def test_extra_args_are_rejected_before_domain(owner, ai_seed):
    seed = ai_seed()
    registry = get_registry()
    base = {"appointment_type": seed["appointment_type"].code, "date": "2030-01-07"}

    result = registry.execute(
        _ctx(owner), "check_availability", {**base, "clinic_id": str(owner.clinic.id)}
    )
    assert result.error_code == "invalid_arguments"

    result = registry.execute(
        _ctx(owner), "check_availability", {**base, "patient_id": str(seed["patient"].id)}
    )
    assert result.error_code == "invalid_arguments"


def test_booking_requires_confirmation_then_books(owner, ai_seed, engine):
    seed = ai_seed()
    call = _call(engine, owner, seed["patient"])
    start = timezone.now() + timedelta(days=3)
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()}
    ctx = _ctx(owner, call=call, patient=seed["patient"])

    first = get_registry().execute(ctx, "book_appointment", args)
    assert first.status == "rejected"
    assert first.error_code == "confirmation_required"
    nonce = first.details["confirmation_nonce"]
    assert nonce

    second = get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    )
    assert second.ok, second.error_code
    assert second.result["duplicate"] is False
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 1


def test_confirmation_nonce_is_bound_to_the_call(owner, ai_seed, engine):
    seed = ai_seed()
    call_a = _call(engine, owner, seed["patient"])
    call_b = _call(engine, owner, seed["patient"])
    start = timezone.now() + timedelta(days=4)
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()}

    first = get_registry().execute(
        _ctx(owner, call=call_a, patient=seed["patient"]), "book_appointment", args
    )
    nonce_a = first.details["confirmation_nonce"]

    reused = get_registry().execute(
        _ctx(owner, call=call_b, patient=seed["patient"]),
        "book_appointment",
        args,
        confirmed=True,
        confirmation_nonce=nonce_a,
    )
    assert reused.error_code == "confirmation_invalid"
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 0


def test_confirmation_nonce_cannot_be_replayed(owner, ai_seed, engine):
    seed = ai_seed()
    call = _call(engine, owner, seed["patient"])
    start = timezone.now() + timedelta(days=5)
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()}
    ctx = _ctx(owner, call=call, patient=seed["patient"])

    first = get_registry().execute(ctx, "book_appointment", args)
    nonce = first.details["confirmation_nonce"]
    assert get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    ).ok

    replay = get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    )
    assert replay.error_code == "confirmation_invalid"


def test_identity_required_before_booking(owner, ai_seed, engine):
    seed = ai_seed()
    call = _call(engine, owner, seed["patient"], confirmed=False)
    start = timezone.now() + timedelta(days=6)
    result = get_registry().execute(
        _ctx(owner, call=call, patient=None),
        "book_appointment",
        {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()},
    )
    assert result.error_code == "identity_required"
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 0


def test_permission_is_rechecked_at_execution(owner, assistant, ai_seed, engine):
    seed = ai_seed()
    call = engine.start_call(clinic=owner.clinic, patient=seed["patient"], started_by=owner.user)
    call.identity_confirmed = True
    call.save(update_fields=["identity_confirmed", "updated_at"])
    start = timezone.now() + timedelta(days=7)
    ctx = ToolContext(
        clinic=owner.clinic,
        user=assistant.user,
        membership=assistant.membership,
        call=call,
        patient=seed["patient"],
    )
    result = get_registry().execute(
        ctx,
        "book_appointment",
        {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()},
    )
    assert result.error_code == "permission_denied"


def test_cross_clinic_appointment_is_rejected(
    owner, staff_factory, ai_seed, engine, location_factory, provider_factory,
    appointment_type_factory, patient_factory,
):
    seed = ai_seed()
    other = staff_factory(role="owner")
    other_location = location_factory(other.clinic, name="Other")
    other_provider = provider_factory(
        other.clinic, membership=other.membership, display_name="Dr Other"
    )
    other_type = appointment_type_factory(other.clinic)
    other_patient = patient_factory(other.clinic, first_name="Bob", last_name="Other")
    start = timezone.now() + timedelta(days=1)
    foreign = Appointment.all_objects.create(
        clinic=other.clinic,
        location=other_location,
        patient=other_patient,
        provider=other_provider,
        appointment_type=other_type,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status="scheduled",
    )

    call = _call(engine, owner, seed["patient"])
    ctx = _ctx(owner, call=call, patient=seed["patient"])
    first = get_registry().execute(ctx, "cancel_appointment", {"appointment_id": str(foreign.id)})
    assert first.error_code == "confirmation_required"
    result = get_registry().execute(
        ctx,
        "cancel_appointment",
        {"appointment_id": str(foreign.id)},
        confirmed=True,
        confirmation_nonce=first.details["confirmation_nonce"],
    )
    assert result.error_code == "not_found"
    foreign.refresh_from_db()
    assert foreign.status == "scheduled"


def test_duplicate_booking_returns_existing(owner, ai_seed, engine):
    seed = ai_seed()
    call = _call(engine, owner, seed["patient"])
    start = timezone.now() + timedelta(days=8)
    args = {"appointment_type": seed["appointment_type"].code, "start_at": start.isoformat()}
    ctx = _ctx(owner, call=call, patient=seed["patient"])

    first = get_registry().execute(ctx, "book_appointment", args)
    nonce = first.details["confirmation_nonce"]
    assert get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce
    ).ok

    second = get_registry().execute(ctx, "book_appointment", args)
    nonce2 = second.details["confirmation_nonce"]
    booked = get_registry().execute(
        ctx, "book_appointment", args, confirmed=True, confirmation_nonce=nonce2
    )
    assert booked.ok
    assert booked.result["duplicate"] is True
    assert Appointment.all_objects.filter(clinic=owner.clinic).count() == 1


def test_check_availability_returns_slots(owner, ai_seed, location_factory, rule_factory):
    seed = ai_seed()
    future = timezone.now().date() + timedelta(days=7)
    rule_factory(owner.clinic, seed["location"], weekday=future.weekday())
    result = get_registry().execute(
        _ctx(owner),
        "check_availability",
        {"appointment_type": seed["appointment_type"].code, "date": future.isoformat()},
    )
    assert result.ok, result.error_code
    assert result.result["count"] > 0
    assert result.result["slots"][0]["start_at"]


def test_lookup_faq_answers_from_fixed_table(owner, ai_seed):
    ai_seed()
    result = get_registry().execute(_ctx(owner), "lookup_faq", {"query": "opening hours?"})
    assert result.ok
    assert result.result["matched"] is True


def test_escalate_tool_creates_job_and_ends_call(owner, ai_seed, engine):
    seed = ai_seed()
    call = _call(engine, owner, seed["patient"])
    result = get_registry().execute(
        _ctx(owner, call=call, patient=seed["patient"]), "escalate_to_human", {}
    )
    assert result.ok
    assert JobRequest.objects.filter(kind="ai.escalation").exists()
    call.refresh_from_db()
    assert call.status == "escalated"
    assert call.ended_at is not None
