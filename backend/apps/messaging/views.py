"""Messaging API views (T114).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /consents/``, ``POST /consents/{id}/revoke/``
* ``GET /suppressions/``
* ``GET|POST /message-templates/``, ``GET|PATCH /message-templates/{id}/``
* ``GET /messages/?status=&patient=&channel=&purpose=``
* ``GET|POST /recall-types/``, ``GET /recall/due/``,
  ``POST /recall/{patient_id}/schedule/``
* ``POST /webhooks/messaging/{adapter}/`` (HMAC-signed, unauthenticated)

Every staff lookup runs through the tenant-scoped manager, so a foreign id is an
indistinguishable 404. Responses are ``Cache-Control: no-store``: they carry
patient references and delivery state.
"""

from __future__ import annotations

import json

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient

from .adapters import ADAPTER_CHOICES
from .models import Consent, Message, MessageTemplate, RecallType, Suppression
from .serializers import (
    ConsentCreateSerializer,
    ConsentSerializer,
    MessageSerializer,
    MessageTemplateSerializer,
    RecallDueSerializer,
    RecallScheduleSerializer,
    RecallTypeSerializer,
    SuppressionSerializer,
)
from .services import consent as consent_service
from .services import recall as recall_service
from .services import templates as template_service
from .services import webhooks as webhook_service


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class MessagingScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("messaging.view",)

    def clinic(self, request):
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


# --------------------------------------------------------------------------- #
# Consents
# --------------------------------------------------------------------------- #


class ConsentListCreateView(MessagingScopedView):
    allowed_query_params = {"page", "page_size", "patient", "channel", "purpose", "status"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("messaging.view",)
        return ("messaging.manage",)

    @extend_schema(responses=ConsentSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Consent.objects.filter(clinic=clinic).order_by("-granted_at", "-id")
        for field in ("patient", "channel", "purpose", "status"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ConsentSerializer(page, many=True).data)

    @extend_schema(request=ConsentCreateSerializer, responses={201: ConsentSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ConsentCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = Patient.all_objects.get(pk=data["patient"])
        consent = consent_service.grant(
            patient,
            data["channel"],
            data["purpose"],
            source=data.get("source", ""),
            wording_version=data.get("wording_version", ""),
            evidence=data.get("evidence") or {},
            actor=request.user,
            request=request,
        )
        return Response(ConsentSerializer(consent).data, status=status.HTTP_201_CREATED)


class ConsentRevokeView(MessagingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("messaging.manage",)

    @extend_schema(request=None, responses=ConsentSerializer)
    def post(self, request, consent_id):
        clinic = self.clinic(request)
        consent = Consent.objects.filter(clinic=clinic, pk=consent_id).first()
        if consent is None:
            raise _not_found()
        consent = consent_service.revoke(consent, actor=request.user, request=request)
        return Response(ConsentSerializer(consent).data)


# --------------------------------------------------------------------------- #
# Suppressions
# --------------------------------------------------------------------------- #


class SuppressionListView(MessagingScopedView):
    allowed_query_params = {"page", "page_size", "channel", "reason"}

    @extend_schema(responses=SuppressionSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Suppression.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("channel", "reason"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SuppressionSerializer(page, many=True).data)


# --------------------------------------------------------------------------- #
# Message templates
# --------------------------------------------------------------------------- #


class MessageTemplateListCreateView(MessagingScopedView):
    allowed_query_params = {"page", "page_size", "channel", "purpose", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("messaging.view",)
        return ("messaging.manage",)

    @extend_schema(responses=MessageTemplateSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = MessageTemplate.objects.filter(clinic=clinic).order_by("key", "id")
        for field in ("channel", "purpose"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            MessageTemplateSerializer(page, many=True).data
        )

    @extend_schema(
        request=MessageTemplateSerializer, responses={201: MessageTemplateSerializer}
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = MessageTemplateSerializer(
            data=request.data, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        record_audit(
            "messaging.template_created",
            entity=template,
            after=template_service.snapshot(template),
            actor=request.user,
            request=request,
        )
        return Response(
            MessageTemplateSerializer(template).data, status=status.HTTP_201_CREATED
        )


class MessageTemplateDetailView(MessagingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("messaging.view",)
        return ("messaging.manage",)

    def _get(self, request, template_id) -> MessageTemplate:
        template = MessageTemplate.objects.filter(
            clinic=self.clinic(request), pk=template_id
        ).first()
        if template is None:
            raise _not_found()
        return template

    @extend_schema(responses=MessageTemplateSerializer)
    def get(self, request, template_id):
        return Response(MessageTemplateSerializer(self._get(request, template_id)).data)

    @extend_schema(request=MessageTemplateSerializer, responses=MessageTemplateSerializer)
    def patch(self, request, template_id):
        template = self._get(request, template_id)
        serializer = MessageTemplateSerializer(
            template, data=request.data, partial=True, context={"clinic": request.clinic}
        )
        serializer.is_valid(raise_exception=True)
        template = template_service.edit(
            template,
            fields=dict(serializer.validated_data),
            actor=request.user,
            request=request,
        )
        return Response(MessageTemplateSerializer(template).data)


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #


class MessageListView(MessagingScopedView):
    allowed_query_params = {
        "page",
        "page_size",
        "status",
        "patient",
        "channel",
        "purpose",
    }

    @extend_schema(responses=MessageSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Message.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("status", "patient", "channel", "purpose"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(MessageSerializer(page, many=True).data)


# --------------------------------------------------------------------------- #
# Recall
# --------------------------------------------------------------------------- #

RECALL_DUE_SCHEMA = RecallDueSerializer


class RecallTypeListCreateView(MessagingScopedView):
    allowed_query_params = {"page", "page_size", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("recall.manage",)

    @extend_schema(responses=RecallTypeSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = RecallType.objects.filter(clinic=clinic).order_by("name", "id")
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(RecallTypeSerializer(page, many=True).data)

    @extend_schema(request=RecallTypeSerializer, responses={201: RecallTypeSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = RecallTypeSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        recall_type = serializer.save()
        record_audit(
            "messaging.recall_type_created",
            entity=recall_type,
            after={"name": recall_type.name, "interval_months": recall_type.interval_months},
            actor=request.user,
            request=request,
        )
        return Response(
            RecallTypeSerializer(recall_type).data, status=status.HTTP_201_CREATED
        )


class RecallDueView(MessagingScopedView):
    allowed_query_params = {"as_of"}

    # Returns a bare array (no ``page``/``page_size``); opt out of the envelope.
    pagination_class = None

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("recall.manage",)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="as_of", type=str, location=OpenApiParameter.QUERY)
        ],
        responses=RECALL_DUE_SCHEMA(many=True),    )
    def get(self, request):
        clinic = self.clinic(request)
        as_of = None
        raw = (request.query_params.get("as_of") or "").strip()
        if raw:
            from django.utils.dateparse import parse_datetime

            as_of = parse_datetime(raw)
            if as_of is None:
                raise _validation("as_of", "enter a valid ISO-8601 datetime")
        return Response(recall_service.recall_due(clinic, as_of))


class RecallScheduleView(MessagingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("recall.manage",)

    @extend_schema(request=RecallScheduleSerializer, responses=dict)
    def post(self, request, patient_id):
        clinic = self.clinic(request)
        patient = Patient.objects.filter(clinic=clinic, pk=patient_id).first()
        if patient is None:
            raise _not_found()
        serializer = RecallScheduleSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        recall_type = RecallType.objects.get(pk=serializer.validated_data["recall_type"])
        result = recall_service.schedule(
            patient, recall_type, actor=request.user, request=request
        )
        return Response(result, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# Provider webhooks
# --------------------------------------------------------------------------- #

WEBHOOK_SCHEMA = inline_serializer(
    name="MessagingWebhookResult",
    fields={
        "status": serializers.CharField(),
        "event_id": serializers.CharField(required=False),
        "message_status": serializers.CharField(required=False),
        "changed": serializers.BooleanField(required=False),
        "reason": serializers.CharField(required=False),
    },
)


class MessagingWebhookView(NoStoreMixin, APIView):
    """``POST /webhooks/messaging/{adapter}/`` - HMAC-signed provider events."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def _secret(self) -> str:
        from django.conf import settings

        return str(getattr(settings, "MESSAGING_WEBHOOK_SECRET", "") or "")

    @extend_schema(request=None, responses=WEBHOOK_SCHEMA)
    def post(self, request, adapter):
        if adapter not in ADAPTER_CHOICES:
            raise _not_found()
        signature = request.headers.get("X-Messaging-Signature", "")
        timestamp = request.headers.get("X-Messaging-Timestamp", "")
        body = request.body or b""
        if not webhook_service.verify_signature(
            secret=self._secret(),
            timestamp=timestamp,
            body=body,
            signature=signature,
        ):
            raise ApiError(
                "invalid_signature",
                "The webhook signature is not valid.",
                status_code=401,
            )
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise _validation("body", "the request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise _validation("body", "the request body must be a JSON object")
        return Response(webhook_service.process_event(adapter, payload))


__all__ = [
    "ConsentListCreateView",
    "ConsentRevokeView",
    "MessageListView",
    "MessageTemplateDetailView",
    "MessageTemplateListCreateView",
    "MessagingWebhookView",
    "RecallDueView",
    "RecallScheduleView",
    "RecallTypeListCreateView",
    "SuppressionListView",
]
