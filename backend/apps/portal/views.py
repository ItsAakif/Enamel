"""Patient portal API views (T121).

URL layout (mounted under ``/api/v1/``):

* ``POST /portal/auth/request-code/``, ``POST /portal/auth/verify/``,
  ``POST /portal/auth/logout/``
* ``GET /portal/me/``, ``/portal/appointments/``, ``/portal/records/``,
  ``/portal/plans/``, ``/portal/invoices/``, ``/portal/documents/``
* ``GET /portal/appointments/{id}/``, ``/portal/plans/{id}/``,
  ``/portal/invoices/{id}/``, ``/portal/documents/{id}/download/``
* ``POST /portal/appointments/{id}/cancel/``, ``/reschedule/``
* ``POST /portal/plans/{id}/items/{item_id}/{accept|decline}/``

Every authenticated view declares **only** ``PatientSessionAuthentication`` and
``IsPortalAuthenticated``: a staff session cookie is ignored, and a portal cookie
is never accepted by a staff view. Object lookups are scoped by both
``clinic_id`` and ``patient_id`` from ``request.identity``, so a foreign object is
a 404.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice
from apps.billing.services import receipts as billing_receipts
from apps.clinics.models import Clinic
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.patients.models import Patient
from apps.plans.models import Document, PlanItem, TreatmentPlan
from apps.plans.services import documents as document_service
from apps.scheduling.models import Appointment

from .adapters.otp import OUTBOX, echo_allowed
from .authentication import PatientSessionAuthentication, PortalIdentity
from .permissions import IsPortalAuthenticated
from .serializers import (
    PortalAppointmentSerializer,
    PortalDocumentSerializer,
    PortalInvoiceSerializer,
    PortalOtpRequestSerializer,
    PortalOtpVerifySerializer,
    PortalPatientSerializer,
    PortalPlanItemSerializer,
    PortalPlanSerializer,
    PortalRescheduleSerializer,
    PortalSessionSerializer,
)
from .services import identity as identity_service
from .services import otp as otp_service
from .services import receipts as receipt_service
from .services import records as records_service
from .services import self_service

OTP_ACCEPTED_SCHEMA = inline_serializer(
    name="PortalOtpAccepted",
    fields={
        "status": serializers.CharField(),
        "challenge_id": serializers.UUIDField(),
        "code": serializers.CharField(required=False),
        "demo_code": serializers.CharField(
            required=False,
            help_text=(
                "Present only when the server runs with DEMO_MODE: the generated "
                "one-time code is echoed so a public demo of the portal is "
                "usable. A deployment without demo mode never reveals the code."
            ),
        ),
        "demo_notice": serializers.CharField(
            required=False,
            help_text="Present together with demo_code: the demo-mode notice.",
        ),
    },
)

PORTAL_ME_SCHEMA = inline_serializer(
    name="PortalMe",
    fields={
        "patient": PortalPatientSerializer(),
        "clinic_id": serializers.UUIDField(),
    },
)


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


class PortalAuthBaseView(NoStoreMixin, APIView):
    """Unauthenticated portal auth endpoint (no session cookie is read)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]


class PortalScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    """Authenticated portal endpoint; identity comes from the portal cookie."""

    authentication_classes = [PatientSessionAuthentication]
    permission_classes = [IsPortalAuthenticated]

    def identity(self, request) -> PortalIdentity:
        identity = getattr(request, "identity", None)
        if identity is None:
            raise ApiError("unauthenticated", "Authentication is required.", status_code=401)
        return identity


# --- cookie helpers ---------------------------------------------------------


def _set_session_cookie(response: Response, raw_key: str, expires_at) -> None:
    max_age = max(0, int((expires_at - timezone.now()).total_seconds()))
    response.set_cookie(
        identity_service.cookie_name(),
        raw_key,
        max_age=max_age,
        httponly=True,
        samesite="Lax",
        secure=bool(getattr(settings, "SESSION_COOKIE_SECURE", False)),
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(identity_service.cookie_name(), path="/")


# --- auth -------------------------------------------------------------------


class PortalRequestCodeView(PortalAuthBaseView):
    allowed_query_params: set = set()

    @extend_schema(request=PortalOtpRequestSerializer, responses={202: OTP_ACCEPTED_SCHEMA})
    def post(self, request):
        serializer = PortalOtpRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slug = serializer.validated_data["clinic_slug"]
        contact = serializer.validated_data["contact"]

        clinic = Clinic.objects.filter(slug=slug, status=Clinic.Status.ACTIVE).first()
        challenge = None
        code = ""
        if clinic is not None:
            challenge, code = otp_service.request_code_with_code(clinic, contact, request=request)

        # Always the same generic response shape, whether or not the contact is
        # known: an unknown contact gets a random, unusable challenge id.
        body = {
            "status": "accepted",
            "challenge_id": str(challenge.id if challenge is not None else uuid.uuid7()),
        }
        if challenge is not None:
            record_audit(
                "portal.otp_requested",
                entity=challenge,
                clinic=challenge.clinic_id,
                after={"channel": challenge.channel},
                request=request,
            )
            if settings.DEMO_MODE and challenge is not None:
                # The guarded demo deployment reveals the code. The fields are
                # additive and clearly labelled; a production deployment does
                # not run DEMO_MODE (config/settings/prod.py refuses it), so
                # the response shape there is unchanged. An unknown contact
                # gets no demo_code either: it is the same generic response.
                body["demo_code"] = code
                body["demo_notice"] = settings.DEMO_OTP_NOTICE
            elif echo_allowed() and OUTBOX:
                body["code"] = OUTBOX[-1]["code"]
        return Response(body, status=status.HTTP_202_ACCEPTED)


class PortalVerifyView(PortalAuthBaseView):
    allowed_query_params: set = set()

    @extend_schema(request=PortalOtpVerifySerializer, responses=PortalSessionSerializer)
    def post(self, request):
        serializer = PortalOtpVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session, raw_key = otp_service.verify_code(
            serializer.validated_data["challenge_id"],
            serializer.validated_data["code"],
            request=request,
        )
        record_audit(
            "portal.session_created",
            entity=session,
            clinic=session.clinic_id,
            after={"patient_id": str(session.patient_id)},
            request=request,
        )
        response = Response(
            {
                "patient_id": str(session.patient_id),
                "clinic_id": str(session.clinic_id),
                "expires_at": session.expires_at,
            }
        )
        _set_session_cookie(response, raw_key, session.expires_at)
        return response


class PortalLogoutView(PortalScopedView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        session = getattr(request, "portal_session", None)
        if session is not None:
            identity_service.revoke_session(session)
            record_audit(
                "portal.session_revoked",
                entity=session,
                clinic=session.clinic_id,
                after={"patient_id": str(session.patient_id)},
                request=request,
            )
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_session_cookie(response)
        return response


# --- identity and records ---------------------------------------------------


def _patient(identity: PortalIdentity) -> Patient:
    patient = Patient.all_objects.filter(
        clinic_id=identity.clinic_id, pk=identity.patient_id, deleted_at__isnull=True
    ).first()
    if patient is None:
        raise _not_found()
    return patient


class PortalMeView(PortalScopedView):
    @extend_schema(responses=PORTAL_ME_SCHEMA)
    def get(self, request):
        identity = self.identity(request)
        patient = _patient(identity)
        return Response(
            {
                "patient": PortalPatientSerializer(
                    records_service.patient_summary(patient)
                ).data,
                "clinic_id": str(identity.clinic_id),
            }
        )


class PortalRecordsView(PortalScopedView):
    @extend_schema(responses=dict)
    def get(self, request):
        identity = self.identity(request)
        patient = _patient(identity)
        return Response(records_service.record_summary(patient))


# --- appointments -----------------------------------------------------------


def _appointment_resource(appointment: Appointment) -> dict:
    return PortalAppointmentSerializer(
        {
            "id": appointment.id,
            "start_at": appointment.start_at,
            "end_at": appointment.end_at,
            "status": appointment.status,
            "source": appointment.source,
            "reason": appointment.reason,
            "appointment_type_name": getattr(
                appointment.appointment_type, "name", ""
            ),
            "provider_name": getattr(appointment.provider, "display_name", ""),
            "location_name": getattr(appointment.location, "name", ""),
        }
    ).data


class PortalAppointmentsView(PortalScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=PortalAppointmentSerializer(many=True))
    def get(self, request):
        identity = self.identity(request)
        queryset = (
            Appointment.all_objects.filter(
                clinic_id=identity.clinic_id, patient_id=identity.patient_id
            )
            .select_related("appointment_type", "provider", "location")
            .order_by("-start_at", "id")
        )
        value = (request.query_params.get("status") or "").strip()
        if value:
            queryset = queryset.filter(status=value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [_appointment_resource(item) for item in page]
        return paginator.get_paginated_response(data)


class PortalAppointmentDetailView(PortalScopedView):
    @extend_schema(responses=PortalAppointmentSerializer)
    def get(self, request, appointment_id):
        identity = self.identity(request)
        appointment = self_service.resolve_appointment(
            identity.clinic_id, identity.patient_id, appointment_id
        )
        return Response(_appointment_resource(appointment))


class PortalAppointmentCancelView(PortalScopedView):
    @extend_schema(request=None, responses=PortalAppointmentSerializer)
    def post(self, request, appointment_id):
        identity = self.identity(request)
        appointment = self_service.cancel_appointment(
            identity.clinic_id, identity.patient_id, appointment_id, request=request
        )
        return Response(_appointment_resource(appointment))


class PortalAppointmentRescheduleView(PortalScopedView):
    @extend_schema(request=PortalRescheduleSerializer, responses=PortalAppointmentSerializer)
    def post(self, request, appointment_id):
        identity = self.identity(request)
        serializer = PortalRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment = self_service.reschedule_appointment(
            identity.clinic_id,
            identity.patient_id,
            appointment_id,
            serializer.validated_data["start_at"],
            request=request,
        )
        return Response(_appointment_resource(appointment))


# --- plans ------------------------------------------------------------------


def _plan_resource(plan: TreatmentPlan) -> dict:
    items = PlanItem.all_objects.filter(plan_id=plan.pk).order_by("sequence", "id")
    return PortalPlanSerializer(
        {
            "id": plan.id,
            "title": plan.title,
            "status": plan.status,
            "currency": plan.currency,
            "subtotal_minor": plan.subtotal_minor,
            "discount_minor": plan.discount_minor,
            "tax_minor": plan.tax_minor,
            "total_minor": plan.total_minor,
            "expires_at": plan.expires_at,
            "presented_at": plan.presented_at,
            "accepted_at": plan.accepted_at,
            "created_at": plan.created_at,
            "items": [PortalPlanItemSerializer(item).data for item in items],
        }
    ).data


class PortalPlansView(PortalScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=PortalPlanSerializer(many=True))
    def get(self, request):
        identity = self.identity(request)
        queryset = TreatmentPlan.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id
        ).order_by("-created_at", "-id")
        value = (request.query_params.get("status") or "").strip()
        if value:
            queryset = queryset.filter(status=value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [_plan_resource(plan) for plan in page]
        return paginator.get_paginated_response(data)


class PortalPlanDetailView(PortalScopedView):
    @extend_schema(responses=PortalPlanSerializer)
    def get(self, request, plan_id):
        identity = self.identity(request)
        plan = self_service.resolve_plan(identity.clinic_id, identity.patient_id, plan_id)
        return Response(_plan_resource(plan))


class PortalPlanItemActionView(PortalScopedView):
    @extend_schema(request=None, responses=PortalPlanItemSerializer)
    def post(self, request, plan_id, item_id, action):
        identity = self.identity(request)
        item = self_service.set_plan_item_status(
            identity.clinic_id, identity.patient_id, plan_id, item_id, action, request=request
        )
        return Response(PortalPlanItemSerializer(item).data)


# --- invoices ---------------------------------------------------------------


class PortalInvoicesView(PortalScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=PortalInvoiceSerializer(many=True))
    def get(self, request):
        identity = self.identity(request)
        queryset = Invoice.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id
        ).order_by("-created_at", "-id")
        value = (request.query_params.get("status") or "").strip()
        if value:
            queryset = queryset.filter(status=value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [PortalInvoiceSerializer(item).data for item in page]
        return paginator.get_paginated_response(data)


class PortalInvoiceDetailView(PortalScopedView):
    @extend_schema(responses=PortalInvoiceSerializer)
    def get(self, request, invoice_id):
        identity = self.identity(request)
        invoice = Invoice.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id, pk=invoice_id
        ).first()
        if invoice is None:
            raise _not_found()
        return Response(PortalInvoiceSerializer(invoice).data)


class PortalInvoiceReceiptView(PortalScopedView):
    @extend_schema(responses={200: bytes})
    def get(self, request, invoice_id):
        identity = self.identity(request)
        invoice = Invoice.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id, pk=invoice_id
        ).first()
        if invoice is None:
            raise _not_found()

        token = (request.query_params.get("token") or "").strip()
        if not receipt_service.verify_receipt_token(token, invoice=invoice):
            raise ApiError(
                "permission_denied",
                "A valid download link is required.",
                status_code=403,
            )

        data = billing_receipts.render_receipt_pdf(invoice)
        record_audit(
            "portal.receipt_downloaded",
            entity=invoice,
            clinic=invoice.clinic_id,
            after={"invoice_id": str(invoice.id), "number": invoice.number},
            request=request,
        )
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="receipt-{invoice.number}.pdf"'
        response["X-Document-Sha256"] = billing_receipts.content_hash(data)
        response["Cache-Control"] = "no-store"
        return response


# --- documents --------------------------------------------------------------


class PortalDocumentsView(PortalScopedView):
    allowed_query_params = {"page", "page_size", "kind"}

    @extend_schema(responses=PortalDocumentSerializer(many=True))
    def get(self, request):
        identity = self.identity(request)
        queryset = Document.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id
        ).order_by("-created_at", "-id")
        value = (request.query_params.get("kind") or "").strip()
        if value:
            queryset = queryset.filter(kind=value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = [PortalDocumentSerializer(item).data for item in page]
        return paginator.get_paginated_response(data)


class PortalDocumentDownloadView(PortalScopedView):
    @extend_schema(responses={200: bytes})
    def get(self, request, document_id):
        identity = self.identity(request)
        document = Document.all_objects.filter(
            clinic_id=identity.clinic_id, patient_id=identity.patient_id, pk=document_id
        ).first()
        if document is None:
            raise _not_found()

        token = (request.query_params.get("token") or "").strip()
        if not document_service.verify_download_token(token, document=document):
            raise ApiError(
                "permission_denied",
                "A valid download link is required.",
                status_code=403,
            )
        data = document_service.read_bytes(document)
        record_audit(
            "portal.document_downloaded",
            entity=document,
            clinic=document.clinic_id,
            after={"document_id": str(document.id), "sha256": document.sha256},
            request=request,
        )
        response = HttpResponse(data, content_type=document.content_type)
        response["Content-Disposition"] = f'attachment; filename="{document.filename}"'
        response["X-Document-Sha256"] = document.sha256
        response["Cache-Control"] = "no-store"
        return response


__all__ = [
    "PortalAppointmentCancelView",
    "PortalAppointmentDetailView",
    "PortalAppointmentRescheduleView",
    "PortalAppointmentsView",
    "PortalDocumentDownloadView",
    "PortalDocumentsView",
    "PortalInvoiceDetailView",
    "PortalInvoiceReceiptView",
    "PortalInvoicesView",
    "PortalLogoutView",
    "PortalMeView",
    "PortalPlanDetailView",
    "PortalPlanItemActionView",
    "PortalPlansView",
    "PortalRecordsView",
    "PortalRequestCodeView",
    "PortalVerifyView",
]
