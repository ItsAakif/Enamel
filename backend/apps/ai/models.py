"""AI platform models (T123 provider layer).

Two tables, both deliberately content-free:

* ``AIInvocation`` records *what happened* to one provider call -- provider,
  model, prompt key/version, status, latency, token counts, cost, and the
  request/response **hashes**. The prompt text, the completion text and any PHI
  never reach this row (or any log line).
* ``PromptTemplate`` holds only prompt **metadata**. Prompt bodies live in code
  under ``apps/ai/prompts`` and are synced into this table by
  ``prompts.registry.sync_prompt_templates``.

``clinic`` is nullable because a platform-scoped call (a health probe, an eval
run) has no tenant; the tenant-facing API always filters by clinic explicitly.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from pgvector.django import VectorField

from apps.core.tenancy import ClinicScopedModel


class PromptTemplate(models.Model):
    """Metadata for one version of one prompt (never the prompt body)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    key = models.CharField(max_length=128)
    version = models.PositiveIntegerField(default=1)
    purpose = models.CharField(max_length=64, blank=True, default="")
    schema_json = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_prompttemplate"
        ordering = ["key", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "version"],
                name="uniq_prompt_template_key_version",
            ),
        ]
        verbose_name = "Prompt template"
        verbose_name_plural = "Prompt templates"

    def __str__(self) -> str:
        return f"{self.key}@{self.version}"


class AIInvocation(models.Model):
    """One provider call: metrics and hashes only, never content."""

    class Status(models.TextChoices):
        OK = "ok", "Ok"
        ERROR = "error", "Error"
        DEGRADED = "degraded", "Degraded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    clinic = models.ForeignKey(
        "clinics.Clinic",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_invocations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_invocations",
    )
    provider = models.CharField(max_length=32, default="")
    model = models.CharField(max_length=128, default="")
    purpose = models.CharField(max_length=64, default="")
    prompt_key = models.CharField(max_length=128, blank=True, default="")
    prompt_version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    latency_ms = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost_minor = models.PositiveIntegerField(default=0)
    request_hash = models.CharField(max_length=64, blank=True, default="")
    response_hash = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    trace_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_aiinvocation"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["clinic", "created_at"],
                name="ai_inv_clinic_created_idx",
            ),
            models.Index(
                fields=["clinic", "status"],
                name="ai_inv_clinic_status_idx",
            ),
            models.Index(fields=["trace_id"], name="ai_inv_trace_idx"),
        ]
        verbose_name = "AI invocation"
        verbose_name_plural = "AI invocations"

    def __str__(self) -> str:
        return f"{self.provider}/{self.model} [{self.status}] ({self.pk})"


class ToolInvocation(ClinicScopedModel):
    """One tool execution attempt (T124).

    ``args_hash`` and ``result_json`` are stored; ``result_json`` holds only ids,
    statuses and slot times (never free text). ``call`` is nullable so a
    text-chat tool call and a voice tool call share one table.
    """

    class Status(models.TextChoices):
        OK = "ok", "Ok"
        REJECTED = "rejected", "Rejected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_tool_invocations",
    )
    call = models.ForeignKey(
        "ai.VoiceCall",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tool_invocations",
    )
    tool = models.CharField(max_length=64)
    args_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    error_code = models.CharField(max_length=64, blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)
    result_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "ai_toolinvocation"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "tool"], name="ai_tool_clinic_tool_idx"),
            models.Index(fields=["call", "created_at"], name="ai_tool_call_created_idx"),
        ]
        verbose_name = "Tool invocation"
        verbose_name_plural = "Tool invocations"

    def __str__(self) -> str:
        return f"{self.tool} [{self.status}]"


class VoiceCall(ClinicScopedModel):
    """One simulated (or future live) reception call.

    The ``local_test`` adapter is the only adapter implemented in T124; it is
    text-only and labelled ``Simulator`` in the console. ``recording_key`` stays
    empty unless the caller granted recording consent, so no audio can exist
    without consent.

    Extensions beyond the base field list are documented here: ``started_by``
    (the staff user the engine runs as), ``identity_confirmed`` and
    ``confirmation_nonce``/``confirmation_tool`` (the confirmation gate), and
    ``low_confidence_count`` (the ASR repeat-then-escalate budget).
    """

    class Mode(models.TextChoices):
        LOCAL_TEST = "local_test", "Simulator (local test)"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ESCALATED = "escalated", "Escalated"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_voice_calls",
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_voice_calls",
    )
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.LOCAL_TEST)
    direction = models.CharField(
        max_length=16, choices=Direction.choices, default=Direction.INBOUND
    )
    adapter = models.CharField(max_length=32, default="local_test")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    recording_consent = models.BooleanField(default=False)
    recording_key = models.CharField(max_length=255, blank=True, default="")
    identity_confirmed = models.BooleanField(default=False)
    confirmation_nonce = models.CharField(max_length=64, blank=True, default="")
    confirmation_tool = models.CharField(max_length=64, blank=True, default="")
    low_confidence_count = models.PositiveSmallIntegerField(default=0)
    summary = models.TextField(blank=True, default="")
    outcome = models.CharField(max_length=32, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    cost_minor = models.PositiveIntegerField(default=0)
    trace_id = models.CharField(max_length=64, blank=True, default="")
    caller_phone_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "ai_voicecall"
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "started_at"], name="ai_call_clinic_started_idx"),
            models.Index(fields=["clinic", "status"], name="ai_call_clinic_status_idx"),
        ]
        verbose_name = "Voice call"
        verbose_name_plural = "Voice calls"

    def __str__(self) -> str:
        return f"{self.mode} call [{self.status}] ({self.pk})"


class VoiceTurn(ClinicScopedModel):
    """One transcript turn. Text is stored here (policy field), never logged."""

    class Role(models.TextChoices):
        CALLER = "caller", "Caller"
        ASSISTANT = "assistant", "Assistant"
        SYSTEM = "system", "System"

    class Kind(models.TextChoices):
        MESSAGE = "message", "Message"
        CONFIRMATION_REQUEST = "confirmation_request", "Confirmation request"
        ESCALATION = "escalation", "Escalation"
        OUTCOME = "outcome", "Outcome"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="turns")
    role = models.CharField(max_length=16, choices=Role.choices)
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.MESSAGE)
    ordinal = models.PositiveIntegerField(default=0)
    text = models.TextField(blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "ai_voiceturn"
        ordering = ["ordinal", "created_at", "id"]
        indexes = [
            models.Index(fields=["call", "ordinal"], name="ai_turn_call_ordinal_idx"),
        ]
        verbose_name = "Voice turn"
        verbose_name_plural = "Voice turns"

    def __str__(self) -> str:
        return f"{self.role}: {self.text[:32]}"


class VoiceToolCall(ClinicScopedModel):
    """A tool call made during a voice call, with its confirmation state."""

    class Status(models.TextChoices):
        OK = "ok", "Ok"
        REJECTED = "rejected", "Rejected"
        ERROR = "error", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    call = models.ForeignKey(VoiceCall, on_delete=models.CASCADE, related_name="tool_calls")
    turn = models.ForeignKey(
        VoiceTurn,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tool_calls",
    )
    tool = models.CharField(max_length=64)
    args_json = models.JSONField(default=dict, blank=True)
    confirmed = models.BooleanField(default=False)
    result_json = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)

    class Meta:
        db_table = "ai_voicetoolcall"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["call", "tool"], name="ai_vtc_call_tool_idx"),
        ]
        verbose_name = "Voice tool call"
        verbose_name_plural = "Voice tool calls"

    def __str__(self) -> str:
        return f"{self.tool} [{self.status}]"


class KnowledgeDocument(ClinicScopedModel):
    """A clinic-owned knowledge source (T125).

    ``sha256`` is the digest of the *normalized* text, so re-ingesting the same
    content is a no-op: ``(clinic, sha256)`` is unique and the existing document
    (and its chunk ids) is returned unchanged. ``status=archived`` is the soft
    delete -- archiving flips the document and its chunks to ``archived`` so
    retrieval stops returning them immediately.
    """

    class Kind(models.TextChoices):
        POLICY = "policy", "Policy"
        FAQ = "faq", "Faq"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    title = models.CharField(max_length=255)
    source = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.POLICY)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    chunk_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_knowledge_documents",
    )

    class Meta:
        db_table = "ai_knowledgedocument"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "sha256"],
                name="uniq_knowledge_document_clinic_sha",
            ),
        ]
        indexes = [
            models.Index(fields=["clinic", "status"], name="ai_kdoc_clinic_status_idx"),
        ]
        verbose_name = "Knowledge document"
        verbose_name_plural = "Knowledge documents"

    def __str__(self) -> str:
        return self.title


class KnowledgeChunk(ClinicScopedModel):
    """One embedded chunk of a knowledge document (T125).

    ``ordinal`` is the stable position of the chunk inside the document.
    ``metadata_json`` carries deterministic provenance (token/character counts)
    and never free-form model output.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    ordinal = models.PositiveIntegerField(default=0)
    text = models.TextField()
    token_count = models.PositiveIntegerField(default=0)
    metadata_json = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=settings.AI_EMBEDDING_DIM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "ai_knowledgechunk"
        ordering = ["ordinal", "id"]
        indexes = [
            models.Index(fields=["clinic", "document"], name="ai_kchunk_clinic_doc_idx"),
            models.Index(fields=["clinic", "status"], name="ai_kchunk_clinic_status_idx"),
        ]
        verbose_name = "Knowledge chunk"
        verbose_name_plural = "Knowledge chunks"

    def __str__(self) -> str:
        return f"{self.document_id}#{self.ordinal}"


class RetrievalLog(ClinicScopedModel):
    """One retrieval call: metrics and a query hash, never the query text (T125)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_retrieval_logs",
    )
    query_hash = models.CharField(max_length=64, blank=True, default="")
    top_k = models.PositiveIntegerField(default=0)
    result_count = models.PositiveIntegerField(default=0)
    best_score = models.FloatField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "ai_retrievallog"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["clinic", "created_at"], name="ai_retr_clinic_created_idx"),
        ]
        verbose_name = "Retrieval log"
        verbose_name_plural = "Retrieval logs"

    def __str__(self) -> str:
        return f"retrieval {self.query_hash[:12]} ({self.result_count})"


__all__ = [
    "AIInvocation",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "PromptTemplate",
    "RetrievalLog",
    "ToolInvocation",
    "VoiceCall",
    "VoiceToolCall",
    "VoiceTurn",
]
