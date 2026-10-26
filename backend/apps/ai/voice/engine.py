"""Simulated voice conversation engine (T124).

The engine drives the ``greeting -> intent -> identity -> action -> confirmation
-> outcome`` state machine over the ``local_test`` adapters. Two safety rules are
enforced here, deterministically:

* caller text is data: an injection corpus is refused before any intent runs,
  and the engine never executes a consequential tool from caller text alone;
* identity must be confirmed before any patient-specific action, so the
  registry receives ``ctx.patient`` only after the caller proves who they are.

Consequential actions are executed through the shared tool registry with an
explicit confirmation, exactly like a text-chat tool call.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from django.conf import settings
from django.utils import timezone

from ..models import VoiceCall, VoiceTurn
from ..providers.factory import get_provider
from ..services.escalation import escalate_call
from ..tools.base import ToolContext
from ..tools.confirmation import ensure_confirmation_request, next_ordinal
from ..tools.registry import get_registry
from .adapters.local_test import LocalTestAdapter
from .intent import classify, has_identity_hint, is_injection
from .state import State

GREETING_TEXT = "Hello, you have reached the clinic assistant. How can I help today?"
REFUSAL_TEXT = (
    "I cannot help with that. I can book, reschedule or cancel an appointment, "
    "or answer questions about the clinic."
)
REPEAT_TEXT = "Sorry, I did not catch that. Could you repeat it?"
IDENTITY_PROMPT = "Before I can help with that, please confirm your date of birth."
IDENTITY_FAILED = "I could not match that. Could you confirm your date of birth?"
IDENTITY_CONFIRMED = "Thanks, I have confirmed your identity."
ESCALATED_TEXT = "A staff member will follow up shortly."

PATIENT_SPECIFIC_INTENTS = ("book", "reschedule", "cancel")


def hash_phone(value: str | None) -> str:
    if not value:
        return ""
    salt = str(getattr(settings, "SECRET_KEY", "") or "")
    return hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()


class ConversationEngine:
    def __init__(
        self,
        *,
        provider: Any = None,
        adapters: LocalTestAdapter | None = None,
        registry: Any = None,
        low_confidence_threshold: float = 0.5,
        max_low_confidence: int = 2,
    ) -> None:
        self.adapters = adapters or LocalTestAdapter()
        self.telephony = self.adapters.telephony
        self.asr = self.adapters.asr
        self.tts = self.adapters.tts
        self.registry = registry or get_registry()
        self.provider = provider or get_provider()
        self.low_confidence_threshold = float(low_confidence_threshold)
        self.max_low_confidence = int(max_low_confidence)

    # -- helpers ----------------------------------------------------------- #

    def _say(self, call: VoiceCall, text: str, *, kind: str | None = None) -> VoiceTurn:
        return VoiceTurn.all_objects.create(
            clinic_id=call.clinic_id,
            call=call,
            role=VoiceTurn.Role.ASSISTANT,
            kind=kind or VoiceTurn.Kind.MESSAGE,
            ordinal=next_ordinal(call),
            text=text,
        )

    def _record_turn(
        self, call: VoiceCall, *, role: str, text: str, confidence: float | None
    ) -> VoiceTurn:
        return VoiceTurn.all_objects.create(
            clinic_id=call.clinic_id,
            call=call,
            role=role,
            kind=VoiceTurn.Kind.MESSAGE,
            ordinal=next_ordinal(call),
            text=text,
            confidence=confidence,
        )

    def _context(
        self,
        call: VoiceCall,
        *,
        user: Any,
        membership: Any,
        request: Any,
    ) -> ToolContext:
        return ToolContext(
            clinic=call.clinic,
            user=user,
            membership=membership,
            call=call,
            patient=call.patient if call.identity_confirmed else None,
            request=request,
            actor=user,
        )

    @staticmethod
    def _identity_matches(call: VoiceCall, text: str) -> bool:
        patient = call.patient
        if patient is None:
            return False
        if patient.date_of_birth and patient.date_of_birth.isoformat() in (text or ""):
            return True
        if patient.phone_e164:
            digits = re.sub(r"\D", "", text or "")
            phone = re.sub(r"\D", "", patient.phone_e164)
            if len(digits) >= 6 and phone.endswith(digits[-6:]):
                return True
        return False

    def _result(
        self,
        call: VoiceCall,
        *,
        intent: str,
        assistant_turn: VoiceTurn | None,
        **extra: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "call_id": str(call.id),
            "status": call.status,
            "state": extra.pop("state", State.OUTCOME.value),
            "intent": intent,
            "assistant_turn": assistant_turn,
            "escalated": call.status == VoiceCall.Status.ESCALATED,
            "ended": call.status != VoiceCall.Status.ACTIVE,
        }
        payload.update(extra)
        return payload

    # -- lifecycle --------------------------------------------------------- #

    def start_call(
        self,
        *,
        clinic: Any,
        patient: Any = None,
        started_by: Any = None,
        recording_consent: bool = False,
        caller_phone: str = "",
    ) -> VoiceCall:
        call = VoiceCall.all_objects.create(
            clinic=clinic,
            patient=patient,
            started_by=started_by,
            mode=VoiceCall.Mode.LOCAL_TEST,
            direction=VoiceCall.Direction.INBOUND,
            adapter=self.adapters.mode,
            status=VoiceCall.Status.ACTIVE,
            recording_consent=bool(recording_consent),
            caller_phone_hash=hash_phone(caller_phone),
            trace_id=uuid.uuid4().hex,
            started_at=timezone.now(),
        )
        self.telephony.start(call)
        if recording_consent:
            key = self.telephony.record(call, True)
            if key:
                call.recording_key = key
                call.save(update_fields=["recording_key", "updated_at"])
        self._say(call, GREETING_TEXT)
        return call

    # -- turn handling ----------------------------------------------------- #

    def handle_turn(
        self,
        call: VoiceCall,
        *,
        text: str,
        confidence: float | None = None,
        role: str = "caller",
        user: Any = None,
        membership: Any = None,
        request: Any = None,
    ) -> dict[str, Any]:
        if call.status != VoiceCall.Status.ACTIVE:
            return self._result(
                call, intent="ended", assistant_turn=None, state=State.ENDED.value
            )

        # ASR low confidence: ask once, escalate after the budget is spent.
        if confidence is not None and float(confidence) < self.low_confidence_threshold:
            call.low_confidence_count += 1
            call.save(update_fields=["low_confidence_count", "updated_at"])
            if call.low_confidence_count > self.max_low_confidence:
                escalate_call(call, reason_code="low_confidence", actor=user)
                turn = self._say(call, ESCALATED_TEXT, kind=VoiceTurn.Kind.ESCALATION)
                return self._result(
                    call, intent="unknown", assistant_turn=turn, state=State.ESCALATED.value
                )
            turn = self._say(call, REPEAT_TEXT)
            return self._result(call, intent="unknown", assistant_turn=turn, state=State.INTENT.value)

        self._record_turn(call, role=role, text=text, confidence=confidence)

        # Injection: refuse before any intent runs. No tool is invoked.
        if is_injection(text):
            turn = self._say(call, REFUSAL_TEXT)
            return self._result(call, intent="unknown", assistant_turn=turn, refused=True)

        intent = classify(text)

        # Identity gate for patient-specific work.
        if call.patient is not None and not call.identity_confirmed:
            if has_identity_hint(text):
                if self._identity_matches(call, text):
                    call.identity_confirmed = True
                    call.save(update_fields=["identity_confirmed", "updated_at"])
                    turn = self._say(call, IDENTITY_CONFIRMED)
                    return self._result(
                        call, intent="identity", assistant_turn=turn, state=State.ACTION.value
                    )
                turn = self._say(call, IDENTITY_FAILED)
                return self._result(call, intent="identity", assistant_turn=turn)
            if intent in PATIENT_SPECIFIC_INTENTS:
                turn = self._say(call, IDENTITY_PROMPT)
                return self._result(call, intent=intent, assistant_turn=turn, state=State.IDENTITY.value)

        ctx = self._context(call, user=user, membership=membership, request=request)

        if intent == "escalate":
            escalate_call(call, reason_code="caller_request", actor=user)
            turn = self._say(call, ESCALATED_TEXT, kind=VoiceTurn.Kind.ESCALATION)
            return self._result(
                call, intent=intent, assistant_turn=turn, state=State.ESCALATED.value
            )

        if intent == "faq":
            result = self.registry.execute(ctx, "lookup_faq", {"query": text})
            answer = (result.result or {}).get("answer") or REFUSAL_TEXT
            turn = self._say(call, answer)
            return self._result(
                call,
                intent=intent,
                assistant_turn=turn,
                tool="lookup_faq",
                tool_status=result.status,
            )

        if intent == "book":
            if call.patient is not None and call.identity_confirmed:
                nonce = ensure_confirmation_request(call, "book_appointment")
                turn = (
                    VoiceTurn.all_objects.filter(
                        call=call,
                        role=VoiceTurn.Role.ASSISTANT,
                        kind=VoiceTurn.Kind.CONFIRMATION_REQUEST,
                    )
                    .order_by("-ordinal")
                    .first()
                )
                return self._result(
                    call,
                    intent=intent,
                    assistant_turn=turn,
                    state=State.CONFIRMATION.value,
                    confirmation_nonce=nonce,
                )
            turn = self._say(call, "I can pass your request to the clinic team.")
            return self._result(call, intent=intent, assistant_turn=turn)

        if intent == "greeting":
            turn = self._say(call, "Hello! How can I help with your appointment today?")
        elif intent == "confirm":
            turn = self._say(call, "Thanks. Please confirm the action on screen to proceed.")
        elif intent in ("reschedule", "cancel"):
            turn = self._say(
                call,
                "I can help with that once you confirm the appointment and the new time.",
            )
        else:
            turn = self._say(call, REFUSAL_TEXT)

        return self._result(call, intent=intent, assistant_turn=turn)


__all__ = [
    "ESCALATED_TEXT",
    "GREETING_TEXT",
    "IDENTITY_PROMPT",
    "PATIENT_SPECIFIC_INTENTS",
    "REFUSAL_TEXT",
    "REPEAT_TEXT",
    "ConversationEngine",
    "hash_phone",
]
