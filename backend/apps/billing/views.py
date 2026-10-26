"""Billing API views (T119).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /invoices/``, ``GET /invoices/{id}/``, ``GET /invoices/{id}/pdf/``
* ``POST /invoices/from-plan/{plan_id}/``
* ``POST /invoices/{id}/payments/`` (Idempotency-Key required)
* ``POST /payments/{id}/refund/`` (Idempotency-Key required)
* ``POST /webhooks/payments/{gateway}/`` (unauthenticated, HMAC-signed)
* ``GET /billing/reconciliation/``

Every staff lookup runs through the tenant-scoped manager, so a foreign id is an
indistinguishable 404. Responses are ``Cache-Control: no-store``.
"""

from __future__ import annotations

import json

from django.db.models import Q
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ApiError
from apps.core.idempotency import (
    IDEMPOTENCY_HEADER,
    IDEMPOTENCY_KEY_PARAMETER,
    IdempotencyMixin,
)
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient
from apps.plans.models import TreatmentPlan

from .adapters import GATEWAY_CHOICES
from .models import Invoice, Payment, ReconciliationItem
from .serializers import (
    InvoiceCreateSerializer,
    InvoiceSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ReconciliationItemSerializer,
    RefundCreateSerializer,
    RefundSerializer,
)
from .services import invoices as invoice_service
from .services import payments as payment_service
from .services import receipts as receipt_service
from .services import reconcile as reconcile_service
from .services import refunds as refund_service

PDF_SCHEMA = inline_serializer(
    name="ReceiptPdfDocument",
    fields={"detail": serializers.CharField()},
)

WEBHOOK_SCHEMA = inline_serializer(
    name="PaymentWebhookResult",
    fields={
        "status": serializers.CharField(),
        "event_id": serializers.CharField(required=False),
        "payment_status": serializers.CharField(required=False),
        "reason": serializers.CharField(required=False),
    },
)


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class BillingScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("billing.view",)

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
# Invoices
# --------------------------------------------------------------------------- #


class InvoiceListCreateView(BillingScopedView):
    allowed_query_params = {"page", "page_size", "patient", "status", "plan"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("billing.view",)
        return ("billing.manage",)

    @extend_schema(responses=InvoiceSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Invoice.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("patient", "status", "plan"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(InvoiceSerializer(page, many=True).data)

    @extend_schema(
        request=InvoiceCreateSerializer, responses={201: InvoiceSerializer}
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = InvoiceCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = Patient.all_objects.get(pk=data["patient"])
        plan = None
        if data.get("plan"):
            plan = TreatmentPlan.all_objects.get(pk=data["plan"])
        invoice = invoice_service.create_invoice(
            clinic,
            patient=patient,
            lines=[dict(line) for line in data.get("lines", [])],
            currency=data.get("currency") or None,
            plan=plan,
            notes=data.get("notes", ""),
            due_at=data.get("due_at"),
            actor=request.user,
            request=request,
        )
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceDetailView(BillingScopedView):
    @extend_schema(responses=InvoiceSerializer)
    def get(self, request, invoice_id):
        clinic = self.clinic(request)
        invoice = Invoice.objects.filter(clinic=clinic, pk=invoice_id).first()
        if invoice is None:
            raise _not_found()
        return Response(InvoiceSerializer(invoice).data)


class InvoicePdfView(BillingScopedView):
    @extend_schema(responses=PDF_SCHEMA)
    def get(self, request, invoice_id):
        clinic = self.clinic(request)
        invoice = Invoice.objects.filter(clinic=clinic, pk=invoice_id).first()
        if invoice is None:
            raise _not_found()
        data = receipt_service.render_receipt_pdf(invoice)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="receipt-{invoice.number}.pdf"'
        response["X-Document-Sha256"] = receipt_service.content_hash(data)
        return response


class InvoiceFromPlanView(BillingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("billing.manage",)

    @extend_schema(request=None, responses={201: InvoiceSerializer})
    def post(self, request, plan_id):
        clinic = self.clinic(request)
        plan = TreatmentPlan.objects.filter(clinic=clinic, pk=plan_id).first()
        if plan is None:
            raise _not_found()
        invoice = invoice_service.from_plan(
            plan, actor=request.user, request=request
        )
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# Payments and refunds
# --------------------------------------------------------------------------- #


class PaymentCreateView(IdempotencyMixin, BillingScopedView):
    idempotency_required = True

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("billing.manage",)

    @extend_schema(
        request=PaymentCreateSerializer,
        responses={201: PaymentSerializer},
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
    )
    def post(self, request, invoice_id):
        clinic = self.clinic(request)
        invoice = Invoice.objects.filter(clinic=clinic, pk=invoice_id).first()
        if invoice is None:
            raise _not_found()
        serializer = PaymentCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payment = payment_service.record_payment(
            invoice,
            data["amount_minor"],
            method=data["method"],
            gateway=data.get("gateway", ""),
            gateway_payment_id=data.get("gateway_payment_id", ""),
            idempotency_key=(request.headers.get(IDEMPOTENCY_HEADER) or "").strip(),
            metadata=data.get("metadata") or {},
            actor=request.user,
            request=request,
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class RefundCreateView(IdempotencyMixin, BillingScopedView):
    idempotency_required = True

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("billing.refund",)

    @extend_schema(
        request=RefundCreateSerializer,
        responses={201: RefundSerializer},
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
    )
    def post(self, request, payment_id):
        clinic = self.clinic(request)
        payment = Payment.objects.filter(clinic=clinic, pk=payment_id).first()
        if payment is None:
            raise _not_found()
        serializer = RefundCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        refund = refund_service.refund_payment(
            payment,
            data["amount_minor"],
            reason=data.get("reason", ""),
            actor=request.user,
            request=request,
        )
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# Gateway webhooks
# --------------------------------------------------------------------------- #


class PaymentWebhookView(NoStoreMixin, APIView):
    """``POST /webhooks/payments/{gateway}/`` - HMAC-signed gateway events."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def _secret(self) -> str:
        from django.conf import settings

        return str(getattr(settings, "PAYMENT_WEBHOOK_SECRET", "") or "")

    @extend_schema(request=None, responses=WEBHOOK_SCHEMA)
    def post(self, request, gateway):
        if gateway not in GATEWAY_CHOICES:
            raise _not_found()
        signature = request.headers.get("X-Payment-Signature", "")
        timestamp = request.headers.get("X-Payment-Timestamp", "")
        body = request.body or b""
        if not reconcile_service.verify_signature(
            secret=self._secret(),
            timestamp=timestamp,
            body=body,
            signature=signature,
        ):
            raise ApiError(
                "invalid_signature",
                "The webhook signature is not valid.",
                status_code=400,
            )
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"body": ["the request body must be JSON"]},
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"body": ["the request body must be a JSON object"]},
            )
        return Response(reconcile_service.process_event(gateway, payload, signature_valid=True))


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #


class ReconciliationListView(BillingScopedView):
    allowed_query_params = {"page", "page_size", "status", "from", "to"}

    @extend_schema(
        parameters=[
            OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY),
        ],
        responses=ReconciliationItemSerializer(many=True),
    )
    def get(self, request):
        clinic = self.clinic(request)
        # Clinic-scoped items plus unassigned ones: an unmatched gateway event
        # cannot be attributed to a clinic, so it is visible to any operator's
        # queue. The serializer exposes no payload and no patient data.
        queryset = (
            ReconciliationItem.objects.filter(Q(clinic=clinic) | Q(clinic__isnull=True))
            .distinct()
            .order_by("-created_at", "-id")
        )
        status_value = (request.query_params.get("status") or "").strip()
        if status_value:
            queryset = queryset.filter(status=status_value)

        date_from = (request.query_params.get("from") or "").strip()
        date_to = (request.query_params.get("to") or "").strip()
        if bool(date_from) != bool(date_to):
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"from": ["both from and to are required"]},
            )
        if date_from and date_to:
            start = parse_datetime(date_from)
            end = parse_datetime(date_to)
            if start is None or end is None:
                raise ApiError(
                    "validation_error",
                    "The request could not be processed.",
                    status_code=400,
                    field_errors={"from": ["enter a valid ISO-8601 datetime"]},
                )
            queryset = queryset.filter(created_at__gte=start, created_at__lte=end)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ReconciliationItemSerializer(page, many=True).data
        )


__all__ = [
    "InvoiceDetailView",
    "InvoiceFromPlanView",
    "InvoiceListCreateView",
    "InvoicePdfView",
    "PaymentCreateView",
    "PaymentWebhookView",
    "ReconciliationListView",
    "RefundCreateView",
]
