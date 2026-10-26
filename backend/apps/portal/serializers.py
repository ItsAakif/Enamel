"""Portal serializers (T121).

Read serializers are explicit allowlists: they never expose internal notes,
staff-only references or any other patient's data. Write serializers accept only
what the portal flow needs (a clinic slug + contact for the code request, a
challenge id + code to verify).
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.plans.models import Document
from apps.plans.services import documents as document_service

from .services import receipts as receipt_service
from .services.records import PATIENT_FIELDS


class PortalOtpRequestSerializer(serializers.Serializer):
    clinic_slug = serializers.SlugField(max_length=64)
    contact = serializers.CharField(max_length=255)


class PortalOtpVerifySerializer(serializers.Serializer):
    challenge_id = serializers.UUIDField()
    code = serializers.CharField(max_length=12)


class PortalRescheduleSerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()


class PortalPatientSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    preferred_name = serializers.CharField()
    date_of_birth = serializers.DateField(allow_null=True)
    language = serializers.CharField()
    preferred_contact = serializers.CharField()
    phone_e164 = serializers.CharField()
    email = serializers.CharField()


class PortalAppointmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    status = serializers.CharField()
    source = serializers.CharField()
    reason = serializers.CharField()
    appointment_type_name = serializers.CharField()
    provider_name = serializers.CharField()
    location_name = serializers.CharField()


class PortalPlanItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    procedure_code = serializers.CharField()
    description = serializers.CharField()
    tooth = serializers.IntegerField(allow_null=True)
    surfaces = serializers.ListField(child=serializers.CharField())
    quantity = serializers.IntegerField()
    unit_price_minor = serializers.IntegerField()
    line_total_minor = serializers.IntegerField()
    status = serializers.CharField()
    sequence = serializers.IntegerField()


class PortalPlanSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    status = serializers.CharField()
    currency = serializers.CharField()
    subtotal_minor = serializers.IntegerField()
    discount_minor = serializers.IntegerField()
    tax_minor = serializers.IntegerField()
    total_minor = serializers.IntegerField()
    expires_at = serializers.DateTimeField(allow_null=True)
    presented_at = serializers.DateTimeField(allow_null=True)
    accepted_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    items = PortalPlanItemSerializer(many=True)


class PortalInvoiceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    number = serializers.CharField()
    status = serializers.CharField()
    currency = serializers.CharField()
    subtotal_minor = serializers.IntegerField()
    discount_minor = serializers.IntegerField()
    tax_minor = serializers.IntegerField()
    total_minor = serializers.IntegerField()
    paid_minor = serializers.IntegerField()
    balance_minor = serializers.IntegerField()
    issued_at = serializers.DateTimeField(allow_null=True)
    due_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    receipt_url = serializers.SerializerMethodField()

    def get_receipt_url(self, obj: Any) -> str:
        token = receipt_service.issue_receipt_token(obj)
        return f"{receipt_service.receipt_path(obj)}?token={token}"


class PortalDocumentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    kind = serializers.CharField()
    filename = serializers.CharField()
    content_type = serializers.CharField()
    size_bytes = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    download_url = serializers.SerializerMethodField()

    def get_download_url(self, obj: Document) -> str:
        token = document_service.issue_download_token(obj)
        return f"/api/v1/portal/documents/{obj.pk}/download/?token={token}"


class PortalSessionSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    clinic_id = serializers.UUIDField()
    expires_at = serializers.DateTimeField()


__all__ = [
    "PATIENT_FIELDS",
    "PortalAppointmentSerializer",
    "PortalDocumentSerializer",
    "PortalInvoiceSerializer",
    "PortalOtpRequestSerializer",
    "PortalOtpVerifySerializer",
    "PortalPatientSerializer",
    "PortalPlanItemSerializer",
    "PortalPlanSerializer",
    "PortalRescheduleSerializer",
    "PortalSessionSerializer",
]
