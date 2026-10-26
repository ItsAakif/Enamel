"""AI API views (T123).

URL layout (mounted under ``/api/v1/``):

* ``POST /ai/chat/``            -- text assistant turn (``ai.use``)
* ``GET  /ai/invocations/``     -- provider-call log, owner/dentist
* ``GET  /ai/prompts/``         -- prompt metadata only (``ai.configure``)

Every view is tenant-scoped through :class:`HasClinicPermission`; a foreign id
is an indistinguishable 404 and no response body carries prompt or completion
text. Responses are ``Cache-Control: no-store``.
"""

from __future__ import annotations

import time
from pathlib import Path

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ApiError, error_payload
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.core.throttles import AiThrottle

from .models import AIInvocation, KnowledgeDocument, VoiceCall
from .prompts.registry import (
    ASSISTANT_CHAT_KEY,  # noqa: F401  (re-exported for callers)
    get_prompt,
    sync_prompt_templates,
)
from .providers.base import ProviderUnavailable
from .rag.answer import answer_question
from .rag.embed import EmbeddingDimensionError
from .rag.ingest import (
    UnsupportedFileError,
    archive_document,
    ingest_document,
    ingest_file,
)
from .rag.retrieve import search as rag_search
from .resources import call_resource, turn_resource
from .serializers import (
    AIInvocationSerializer,
    CallCreateSerializer,
    ChatRequestSerializer,
    EscalateSerializer,
    KnowledgeDocumentCreateSerializer,
    KnowledgeDocumentSerializer,
    KnowledgeSearchSerializer,
    PromptTemplateSerializer,
    ToolInvokeSerializer,
    TurnCreateSerializer,
)
from .services.budget import BudgetExceeded
from .services.escalation import escalate_call
from .services.invoke import build_messages, invoke
from .tools.base import ToolContext
from .tools.registry import get_registry


def _clinic(request):
    clinic = getattr(request, "clinic", None)
    if clinic is None:
        scope_error = getattr(request, "clinic_scope_error", None)
        if scope_error is not None:
            raise scope_error
        raise ApiError(
            "permission_denied",
            "You do not have permission to perform this action.",
            status_code=403,
        )
    return clinic


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class AiScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("ai.use",)


CHAT_RESPONSE_SCHEMA = inline_serializer(
    name="AiChatResponse",
    fields={
        "answer": serializers.CharField(),
        "citations": serializers.ListField(child=serializers.DictField()),
        "usage": serializers.DictField(),
        "model": serializers.CharField(),
        "provider": serializers.CharField(),
        "degraded": serializers.BooleanField(),
        "trace_id": serializers.CharField(),
        "refused": serializers.BooleanField(),
    },
)


class ChatView(AiScopedView):
    throttle_classes = [AiThrottle]

    @extend_schema(
        request=ChatRequestSerializer,
        responses={200: CHAT_RESPONSE_SCHEMA},
        operation_id="ai_chat_create",
    )
    def post(self, request):
        clinic = _clinic(request)
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        if payload.get("use_knowledge"):
            return self._knowledge_answer(request, clinic, payload)

        prompt = get_prompt(ASSISTANT_CHAT_KEY)
        messages = build_messages(prompt, payload["message"])

        try:
            result = invoke(
                purpose=payload.get("purpose") or "chat",
                messages=messages,
                clinic=clinic,
                user=request.user,
                prompt_key=ASSISTANT_CHAT_KEY,
                trace_id=getattr(request, "request_id", None),
            )
        except BudgetExceeded as exc:
            raise ApiError(
                exc.code,
                "The clinic AI budget for this month has been reached.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                details={
                    "spent_minor": exc.spent_minor,
                    "budget_minor": exc.budget_minor,
                },
            ) from exc
        except ProviderUnavailable as exc:
            raise ApiError(
                "provider_unavailable",
                "The AI provider is temporarily unavailable.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

        return Response(
            {
                "answer": result.answer,
                "citations": result.citations,
                "usage": {
                    "prompt_tokens": result.usage.prompt_tokens,
                    "completion_tokens": result.usage.completion_tokens,
                    "total_tokens": result.usage.total_tokens,
                },
                "model": result.model,
                "provider": result.provider,
                "degraded": result.degraded,
                "trace_id": result.trace_id,
                "refused": False,
            }
        )

    def _knowledge_answer(self, request, clinic, payload):
        """RAG-grounded chat turn: answer + citations, with refusal below threshold.

        The citations are exactly the chunks the answer cites with its ``[n]``
        markers, and they are always a subset of the retrieved set that was
        placed in the prompt. An answer that cites nothing has no citations.
        """

        try:
            grounded = answer_question(
                clinic,
                payload["message"],
                user=request.user,
                trace_id=getattr(request, "request_id", None),
            )
        except BudgetExceeded as exc:
            raise ApiError(
                exc.code,
                "The clinic AI budget for this month has been reached.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                details={
                    "spent_minor": exc.spent_minor,
                    "budget_minor": exc.budget_minor,
                },
            ) from exc
        except ProviderUnavailable as exc:
            raise ApiError(
                "provider_unavailable",
                "The AI provider is temporarily unavailable.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

        return Response(
            {
                "answer": grounded.answer,
                "citations": grounded.citations,
                "usage": {
                    "prompt_tokens": grounded.usage.prompt_tokens,
                    "completion_tokens": grounded.usage.completion_tokens,
                    "total_tokens": grounded.usage.total_tokens,
                },
                "model": grounded.model,
                "provider": grounded.provider,
                "degraded": grounded.degraded,
                "trace_id": grounded.trace_id,
                "refused": grounded.refused,
            }
        )


class InvocationListView(AiScopedView):
    allowed_query_params = {"page", "page_size", "status", "purpose", "ordering"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        # Base auth + clinic scope; the role gate below narrows to owner/dentist.
        return ()

    @extend_schema(responses=AIInvocationSerializer(many=True), operation_id="ai_invocations_list")
    def get(self, request):
        clinic = _clinic(request)
        membership = getattr(request, "membership", None)
        role = getattr(membership, "role", "")
        if role not in ("owner", "dentist"):
            raise ApiError(
                "permission_denied",
                "You do not have permission to perform this action.",
                status_code=403,
            )

        queryset = AIInvocation.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("status", "purpose"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        ordering = (request.query_params.get("ordering") or "").strip()
        if ordering in ("created_at", "-created_at", "latency_ms", "-latency_ms"):
            queryset = queryset.order_by(ordering, "-id")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AIInvocationSerializer(page, many=True).data)


class PromptListView(AiScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("ai.configure",)

    # Returns a bare array (no pagination); opt out of the envelope.
    pagination_class = None

    @extend_schema(responses=PromptTemplateSerializer(many=True), operation_id="ai_prompts_list")
    def get(self, request):
        _clinic(request)
        rows = sync_prompt_templates()
        return Response(PromptTemplateSerializer(rows, many=True).data)


# --------------------------------------------------------------------------- #
# Knowledge base (T125)
# --------------------------------------------------------------------------- #


def _get_document(request, document_id):
    clinic = _clinic(request)
    document = KnowledgeDocument.all_objects.filter(clinic=clinic, pk=document_id).first()
    if document is None:
        raise ApiError("not_found", "Not found.", status_code=404)
    return document


class KnowledgeDocumentListCreateView(AiScopedView):
    allowed_query_params = {"page", "page_size", "status", "kind", "ordering"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        # Listing is a read; ingestion changes the knowledge base.
        if request.method == "POST":
            return ("ai.configure",)
        return ("ai.use",)

    @extend_schema(
        responses=KnowledgeDocumentSerializer(many=True),
        operation_id="ai_knowledge_documents_list",
    )
    def get(self, request):
        clinic = _clinic(request)
        queryset = KnowledgeDocument.all_objects.filter(clinic=clinic).order_by(
            "-created_at", "-id"
        )
        status_value = (request.query_params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)
        kind_value = (request.query_params.get("kind") or "").strip()
        if kind_value:
            queryset = queryset.filter(kind=kind_value)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            KnowledgeDocumentSerializer(page, many=True).data
        )

    @extend_schema(
        request=KnowledgeDocumentCreateSerializer,
        responses={201: KnowledgeDocumentSerializer},
        operation_id="ai_knowledge_documents_create",
    )
    def post(self, request):
        clinic = _clinic(request)
        upload = request.FILES.get("file")
        serializer = KnowledgeDocumentCreateSerializer(
            data=request.data, context={"has_file": bool(upload)}
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        try:
            if upload is not None:
                result = ingest_file(
                    clinic,
                    title=payload.get("title") or Path(upload.name).stem,
                    filename=upload.name,
                    content=upload.read(),
                    source=payload.get("source") or upload.name,
                    kind=payload.get("kind") or "policy",
                    created_by=request.user,
                )
            else:
                result = ingest_document(
                    clinic,
                    title=payload.get("title") or "Untitled document",
                    text=payload.get("text") or "",
                    source=payload.get("source") or "",
                    kind=payload.get("kind") or "policy",
                    created_by=request.user,
                )
        except (UnsupportedFileError, EmbeddingDimensionError) as exc:
            raise ApiError("validation_error", str(exc), status_code=400) from exc

        status_code = 201 if result.created else 200
        return Response(
            KnowledgeDocumentSerializer(result.document).data, status=status_code
        )


class KnowledgeDocumentDetailView(AiScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("ai.configure",)

    @extend_schema(responses={204: None}, operation_id="ai_knowledge_documents_archive")
    def delete(self, request, document_id):
        document = _get_document(request, document_id)
        archive_document(document)
        return Response(status=204)


class KnowledgeSearchView(AiScopedView):
    @extend_schema(
        request=KnowledgeSearchSerializer,
        responses={200: dict},
        operation_id="ai_knowledge_search",
    )
    def post(self, request):
        clinic = _clinic(request)
        serializer = KnowledgeSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        results = rag_search(
            clinic, payload["query"], top_k=payload["top_k"], user=request.user
        )
        return Response(
            {
                "count": len(results),
                "results": [
                    {
                        "chunk_id": item.chunk_id,
                        "document_id": item.document_id,
                        "title": item.title,
                        "kind": item.kind,
                        "ordinal": item.ordinal,
                        "score": round(item.score, 6),
                        "text": item.text,
                        "token_count": item.token_count,
                    }
                    for item in results
                ],
            }
        )


# --------------------------------------------------------------------------- #
# Voice calls (T124)
# --------------------------------------------------------------------------- #


_ENGINE = None

TOOL_HTTP_STATUS: dict[str, int] = {
    "unknown_tool": 400,
    "invalid_arguments": 400,
    "permission_denied": 403,
    "not_found": 404,
    "identity_required": 409,
    "confirmation_required": 409,
    "confirmation_invalid": 409,
    "slot_conflict": 409,
    "no_provider": 409,
    "no_location": 409,
    "invalid_transition": 409,
}

TOOL_OUTCOMES: dict[str, tuple[str, str]] = {
    "book_appointment": ("booked", "Appointment booked."),
    "reschedule_appointment": ("rescheduled", "Appointment rescheduled."),
    "cancel_appointment": ("cancelled", "Appointment cancelled."),
    "lookup_faq": ("answered", "Clinic question answered."),
    "escalate_to_human": ("escalated", "Escalated to staff."),
}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from .voice.engine import ConversationEngine

        _ENGINE = ConversationEngine()
    return _ENGINE


def _get_call(request, call_id):
    clinic = _clinic(request)
    call = VoiceCall.all_objects.filter(clinic=clinic, pk=call_id).first()
    if call is None:
        raise ApiError("not_found", "Not found.", status_code=404)
    return call


def _tool_response(request, result):
    if result.ok:
        return Response(
            {
                "status": "ok",
                "tool": result.tool,
                "result": result.result,
                "invocation_id": result.details.get("invocation_id"),
            }
        )
    status_code = TOOL_HTTP_STATUS.get(result.error_code, 409)
    if result.status == "error":
        status_code = 500
    details = {key: value for key, value in result.details.items() if key != "field_errors"}
    field_errors = result.details.get("field_errors") or {}
    return Response(
        error_payload(
            request,
            result.error_code or "tool_error",
            result.message or "The tool call could not be completed.",
            field_errors=field_errors,
            details=details,
        ),
        status=status_code,
    )


def _apply_tool_outcome(call, tool, result) -> None:
    if not result.ok:
        return
    outcome, summary = TOOL_OUTCOMES.get(tool, ("", ""))
    if outcome:
        call.outcome = outcome
    if summary:
        call.summary = summary
    call.save(update_fields=["outcome", "summary", "updated_at"])


class CallListCreateView(AiScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=dict, operation_id="ai_calls_list")
    def get(self, request):
        clinic = _clinic(request)
        queryset = VoiceCall.all_objects.filter(clinic=clinic).order_by("-started_at", "-id")
        status_value = (request.query_params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response([call_resource(call) for call in page])

    @extend_schema(
        request=CallCreateSerializer, responses={201: dict}, operation_id="ai_calls_create"
    )
    def post(self, request):
        clinic = _clinic(request)
        serializer = CallCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        patient = None
        if payload.get("patient_id"):
            from apps.patients.models import Patient

            patient = Patient.all_objects.filter(
                clinic=clinic, pk=payload["patient_id"], deleted_at__isnull=True
            ).first()
            if patient is None:
                raise ApiError("not_found", "Not found.", status_code=404)

        call = _engine().start_call(
            clinic=clinic,
            patient=patient,
            started_by=request.user,
            recording_consent=payload.get("recording_consent", False),
            caller_phone=payload.get("caller_phone", ""),
        )
        return Response(call_resource(call, include_children=True), status=201)


class CallDetailView(AiScopedView):
    @extend_schema(responses=dict, operation_id="ai_calls_retrieve")
    def get(self, request, call_id):
        call = _get_call(request, call_id)
        return Response(call_resource(call, include_children=True))


class CallTurnCreateView(AiScopedView):
    @extend_schema(
        request=TurnCreateSerializer, responses=dict, operation_id="ai_calls_turns_create"
    )
    def post(self, request, call_id):
        call = _get_call(request, call_id)
        serializer = TurnCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        started = time.perf_counter()
        outcome = _engine().handle_turn(
            call,
            text=payload["text"],
            confidence=payload.get("confidence"),
            role=payload["role"],
            user=request.user,
            membership=getattr(request, "membership", None),
            request=request,
        )
        call.latency_ms += int((time.perf_counter() - started) * 1000)
        call.save(update_fields=["latency_ms", "updated_at"])

        turn = outcome.get("assistant_turn")
        return Response(
            {
                "call": call_resource(call),
                "assistant_turn": turn_resource(turn) if turn is not None else None,
                "intent": outcome.get("intent"),
                "state": outcome.get("state"),
                "escalated": outcome.get("escalated", False),
                "refused": outcome.get("refused", False),
                "confirmation_nonce": outcome.get("confirmation_nonce", ""),
            }
        )


class CallToolInvokeView(AiScopedView):
    @extend_schema(
        request=ToolInvokeSerializer, responses=dict, operation_id="ai_calls_tools_invoke"
    )
    def post(self, request, call_id, tool):
        call = _get_call(request, call_id)
        serializer = ToolInvokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        ctx = ToolContext(
            clinic=call.clinic,
            user=request.user,
            membership=getattr(request, "membership", None),
            call=call,
            patient=call.patient if call.identity_confirmed else None,
            request=request,
            actor=request.user,
        )
        started = time.perf_counter()
        result = get_registry().execute(
            ctx,
            tool,
            payload.get("args") or {},
            confirmed=payload.get("confirmed", False),
            confirmation_nonce=payload.get("confirmation_nonce", ""),
        )
        call.latency_ms += int((time.perf_counter() - started) * 1000)
        call.save(update_fields=["latency_ms", "updated_at"])
        _apply_tool_outcome(call, tool, result)
        return _tool_response(request, result)


class CallEscalateView(AiScopedView):
    @extend_schema(
        request=EscalateSerializer, responses=dict, operation_id="ai_calls_escalate"
    )
    def post(self, request, call_id):
        call = _get_call(request, call_id)
        serializer = EscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = escalate_call(
            call,
            reason_code=serializer.validated_data.get("reason_code") or "caller_request",
            actor=request.user,
        )
        return Response(
            {
                "escalation_id": str(job.id),
                "status": "escalated",
                "call": call_resource(call),
            }
        )


__all__ = [
    "AiScopedView",
    "CallDetailView",
    "CallEscalateView",
    "CallListCreateView",
    "CallToolInvokeView",
    "CallTurnCreateView",
    "ChatView",
    "InvocationListView",
    "KnowledgeDocumentDetailView",
    "KnowledgeDocumentListCreateView",
    "KnowledgeSearchView",
    "PromptListView",
]
