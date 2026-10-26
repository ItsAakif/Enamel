"""AI API serializers (T123).

Input shapes only: the response projections are declared inline in the views so
the OpenAPI contract stays explicit. No field here can carry a clinic id or a
patient id from the client -- those come from the request scope and the call
context.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import AIInvocation, KnowledgeDocument, PromptTemplate


class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)
    purpose = serializers.CharField(max_length=64, required=False, allow_blank=True, default="chat")
    use_knowledge = serializers.BooleanField(required=False, default=False)

    def validate_message(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class AIInvocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIInvocation
        fields = (
            "id",
            "provider",
            "model",
            "purpose",
            "prompt_key",
            "prompt_version",
            "status",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "cost_minor",
            "request_hash",
            "response_hash",
            "error_code",
            "trace_id",
            "created_at",
        )
        read_only_fields = fields


class PromptTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptTemplate
        fields = ("key", "version", "purpose", "schema_json", "is_active")
        read_only_fields = fields


class CallCreateSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    recording_consent = serializers.BooleanField(required=False, default=False)
    caller_phone = serializers.CharField(required=False, allow_blank=True, default="", max_length=32)


class TurnCreateSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=4000)
    confidence = serializers.FloatField(required=False, allow_null=True, default=None)
    role = serializers.ChoiceField(choices=["caller"], default="caller")

    def validate_text(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class ToolInvokeSerializer(serializers.Serializer):
    args = serializers.DictField(required=False, default=dict)
    confirmed = serializers.BooleanField(required=False, default=False)
    confirmation_nonce = serializers.CharField(required=False, allow_blank=True, default="")


class EscalateSerializer(serializers.Serializer):
    reason_code = serializers.CharField(
        required=False, allow_blank=True, default="caller_request", max_length=64
    )


class KnowledgeDocumentCreateSerializer(serializers.Serializer):
    """Text ingestion input. A ``file`` upload is read from ``request.FILES``."""

    title = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    text = serializers.CharField(required=False, allow_blank=True, default="")
    source = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    kind = serializers.ChoiceField(
        choices=["policy", "faq"], required=False, default="policy"
    )

    def validate(self, attrs):
        has_file = bool(self.context.get("has_file"))
        if not has_file and not (attrs.get("text") or "").strip():
            raise serializers.ValidationError(
                {"text": ["Provide text or upload a file."]}
            )
        return attrs


class KnowledgeSearchSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=2000)
    top_k = serializers.IntegerField(required=False, min_value=1, max_value=50, default=5)

    def validate_query(self, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError("This field may not be blank.")
        return cleaned


class KnowledgeDocumentSerializer(serializers.ModelSerializer):
    """Document metadata projection. Never exposes the raw content or vectors."""

    class Meta:
        model = KnowledgeDocument
        fields = (
            "id",
            "title",
            "source",
            "kind",
            "sha256",
            "size_bytes",
            "status",
            "chunk_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


__all__ = [
    "AIInvocationSerializer",
    "CallCreateSerializer",
    "ChatRequestSerializer",
    "EscalateSerializer",
    "KnowledgeDocumentCreateSerializer",
    "KnowledgeDocumentSerializer",
    "KnowledgeSearchSerializer",
    "PromptTemplateSerializer",
    "ToolInvokeSerializer",
    "TurnCreateSerializer",
]
