"""Plans API views (T117).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /treatment-plans/``, ``GET|PATCH /treatment-plans/{id}/``
* ``POST /treatment-plans/{id}/{present|accept|decline}/``
* ``GET|POST /consent-templates/``
* ``POST /consent-records/`` (plan consent; see the note below)
* ``GET /consents/{id}/pdf/``
* ``GET|POST /documents/``, ``GET /documents/{id}/download/``

Note on the consent path: ``messaging`` already owns ``/consents/`` (its own
patient-channel-purpose consent registry) and is mounted first, so the plan
consent *create* endpoint lives at ``/consent-records/`` to avoid shadowing it.
The signed-consent PDF route (``/consents/{id}/pdf/``) does not collide and
keeps the documented path.

Every staff lookup runs through the tenant-scoped manager, so a foreign id is an
indistinguishable 404. Responses are ``Cache-Control: no-store``.
"""

from __future__ import annotations

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient

from .models import ConsentRecord, ConsentTemplate, Document, TreatmentPlan
from .serializers import (
    ConsentRecordSerializer,
    ConsentTemplateSerializer,
    DocumentSerializer,
    PlanConsentCreateSerializer,
    TreatmentPlanCreateSerializer,
    TreatmentPlanSerializer,
    TreatmentPlanUpdateSerializer,
)
from .services import consent as consent_service
from .services import documents as document_service
from .services import pdf as pdf_service
from .services import plans as plan_service
from .services import signature as signature_service

PDF_SCHEMA = inline_serializer(
    name="PlanPdfDocument",
    fields={"detail": serializers.CharField()},
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


class PlansScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("plans.view",)

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

    def _item_specs(self, serializer) -> list[dict]:
        specs = []
        for item in serializer.validated_data.get("items", []) or []:
            spec = dict(item)
            spec["tooth_state_id"] = spec.pop("tooth_state", None)
            specs.append(spec)
        return specs


# --------------------------------------------------------------------------- #
# Treatment plans
# --------------------------------------------------------------------------- #


class TreatmentPlanListCreateView(PlansScopedView):
    allowed_query_params = {"page", "page_size", "patient", "status"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("plans.view",)
        return ("plans.edit",)

    @extend_schema(responses=TreatmentPlanSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = TreatmentPlan.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("patient", "status"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            TreatmentPlanSerializer(page, many=True).data
        )

    @extend_schema(
        request=TreatmentPlanCreateSerializer,
        responses={201: TreatmentPlanSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = TreatmentPlanCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = Patient.all_objects.get(pk=data["patient"])
        plan = plan_service.create_plan(
            clinic,
            patient=patient,
            title=data["title"],
            items=self._item_specs(serializer),
            currency=data.get("currency") or None,
            notes=data.get("notes", ""),
            provider=data.get("provider"),
            expires_at=data.get("expires_at"),
            actor=request.user,
            request=request,
        )
        return Response(
            TreatmentPlanSerializer(plan).data, status=status.HTTP_201_CREATED
        )


class TreatmentPlanDetailView(PlansScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("plans.view",)
        return ("plans.edit",)

    def _get(self, request, plan_id) -> TreatmentPlan:
        plan = TreatmentPlan.objects.filter(
            clinic=self.clinic(request), pk=plan_id
        ).first()
        if plan is None:
            raise _not_found()
        return plan

    @extend_schema(responses=TreatmentPlanSerializer)
    def get(self, request, plan_id):
        return Response(TreatmentPlanSerializer(self._get(request, plan_id)).data)

    @extend_schema(
        request=TreatmentPlanUpdateSerializer, responses=TreatmentPlanSerializer
    )
    def patch(self, request, plan_id):
        plan = self._get(request, plan_id)
        serializer = TreatmentPlanUpdateSerializer(
            data=request.data, context={"clinic": request.clinic}
        )
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        items = None
        if "items" in data:
            items = self._item_specs(serializer)
        plan = plan_service.update_plan(
            plan,
            fields=data,
            items=items,
            actor=request.user,
            request=request,
        )
        return Response(TreatmentPlanSerializer(plan).data)


class TreatmentPlanActionView(PlansScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("plans.approve",)

    @extend_schema(
        operation_id="treatment_plans_action",
        request=None,
        responses=TreatmentPlanSerializer,
    )
    def post(self, request, plan_id, action):
        if action not in plan_service.ACTION_TARGETS:
            raise _not_found()
        clinic = self.clinic(request)
        plan = TreatmentPlan.objects.filter(clinic=clinic, pk=plan_id).first()
        if plan is None:
            raise _not_found()
        plan = plan_service.transition(
            plan, action, actor=request.user, request=request
        )
        return Response(TreatmentPlanSerializer(plan).data)


# --------------------------------------------------------------------------- #
# Consent templates
# --------------------------------------------------------------------------- #


class ConsentTemplateListCreateView(PlansScopedView):
    allowed_query_params = {"page", "page_size", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("consent.templates",)

    @extend_schema(responses=ConsentTemplateSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = ConsentTemplate.objects.filter(clinic=clinic).order_by("key", "id")
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ConsentTemplateSerializer(page, many=True).data
        )

    @extend_schema(
        request=ConsentTemplateSerializer,
        responses={201: ConsentTemplateSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ConsentTemplateSerializer(
            data=request.data, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        template = serializer.save()
        record_audit(
            "consent.template_created",
            entity=template,
            after=consent_service.snapshot(template),
            actor=request.user,
            request=request,
        )
        return Response(
            ConsentTemplateSerializer(template).data, status=status.HTTP_201_CREATED
        )


class ConsentTemplateDetailView(PlansScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("consent.templates",)

    def _get(self, request, template_id) -> ConsentTemplate:
        template = ConsentTemplate.objects.filter(
            clinic=self.clinic(request), pk=template_id
        ).first()
        if template is None:
            raise _not_found()
        return template

    @extend_schema(responses=ConsentTemplateSerializer)
    def get(self, request, template_id):
        return Response(ConsentTemplateSerializer(self._get(request, template_id)).data)

    @extend_schema(
        request=ConsentTemplateSerializer, responses=ConsentTemplateSerializer
    )
    def patch(self, request, template_id):
        template = self._get(request, template_id)
        serializer = ConsentTemplateSerializer(
            template, data=request.data, partial=True, context={"clinic": request.clinic}
        )
        serializer.is_valid(raise_exception=True)
        template = consent_service.edit_template(
            template,
            fields=dict(serializer.validated_data),
            actor=request.user,
            request=request,
        )
        return Response(ConsentTemplateSerializer(template).data)


# --------------------------------------------------------------------------- #
# Consent records
# --------------------------------------------------------------------------- #


class ConsentRecordListCreateView(PlansScopedView):
    allowed_query_params = {"page", "page_size", "patient", "status"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("plans.view",)
        return ("plans.edit",)

    @extend_schema(responses=ConsentRecordSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = ConsentRecord.objects.filter(clinic=clinic).order_by(
            "-signed_at", "-id"
        )
        for field in ("patient", "status"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ConsentRecordSerializer(page, many=True).data
        )

    @extend_schema(
        request=PlanConsentCreateSerializer,
        responses={201: ConsentRecordSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = PlanConsentCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        patient = Patient.all_objects.get(pk=data["patient"])
        template = ConsentTemplate.all_objects.get(pk=data["template"])
        document = None
        if data.get("signature_document"):
            document = Document.all_objects.get(pk=data["signature_document"])
        record = signature_service.sign(
            patient,
            template,
            signer_name=data["signer_name"],
            relationship=data.get("relationship", ""),
            signature_type=data.get("signature_type"),
            document=document,
            actor=request.user,
            request=request,
        )
        return Response(
            ConsentRecordSerializer(record).data, status=status.HTTP_201_CREATED
        )


class ConsentPdfView(PlansScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("plans.view",)

    @extend_schema(responses=PDF_SCHEMA)
    def get(self, request, record_id):
        clinic = self.clinic(request)
        record = ConsentRecord.objects.filter(clinic=clinic, pk=record_id).first()
        if record is None:
            raise _not_found()
        data = pdf_service.render_consent_pdf(record)
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="consent-{record.pk}.pdf"'
        response["X-Document-Sha256"] = pdf_service.content_hash(data)
        return response


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


class DocumentListCreateView(PlansScopedView):
    allowed_query_params = {"page", "page_size", "patient", "kind"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("plans.view",)
        return ("plans.edit",)

    @extend_schema(responses=DocumentSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Document.objects.filter(clinic=clinic).order_by("-created_at", "-id")
        for field in ("patient", "kind"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(DocumentSerializer(page, many=True).data)

    @extend_schema(request=None, responses={201: DocumentSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise _validation("file", "a file is required")
        data = upload.read()

        patient = None
        patient_id = (request.data.get("patient") or "").strip()
        if patient_id:
            patient = Patient.objects.filter(clinic=clinic, pk=patient_id).first()
            if patient is None:
                raise _validation("patient", "patient not found in this clinic")

        plan = None
        plan_id = (request.data.get("plan") or "").strip()
        if plan_id:
            plan = TreatmentPlan.objects.filter(clinic=clinic, pk=plan_id).first()
            if plan is None:
                raise _validation("plan", "plan not found in this clinic")

        consent = None
        consent_id = (request.data.get("consent") or "").strip()
        if consent_id:
            consent = ConsentRecord.objects.filter(clinic=clinic, pk=consent_id).first()
            if consent is None:
                raise _validation("consent", "consent not found in this clinic")

        document = document_service.upload(
            clinic=clinic,
            patient=patient,
            kind=(request.data.get("kind") or "other"),
            filename=upload.name or "upload",
            content_type=upload.content_type or "application/octet-stream",
            data=data,
            uploaded_by=request.user,
            plan=plan,
            consent=consent,
            actor=request.user,
            request=request,
        )
        return Response(
            DocumentSerializer(document).data, status=status.HTTP_201_CREATED
        )


class DocumentDownloadView(PlansScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("plans.view",)

    @extend_schema(responses=PDF_SCHEMA)
    def get(self, request, document_id):
        clinic = self.clinic(request)
        document = Document.objects.filter(clinic=clinic, pk=document_id).first()
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
        response = HttpResponse(data, content_type=document.content_type)
        response["Content-Disposition"] = f'attachment; filename="{document.filename}"'
        response["X-Document-Sha256"] = document.sha256
        return response


__all__ = [
    "ConsentPdfView",
    "ConsentRecordListCreateView",
    "ConsentTemplateDetailView",
    "ConsentTemplateListCreateView",
    "DocumentDownloadView",
    "DocumentListCreateView",
    "TreatmentPlanActionView",
    "TreatmentPlanDetailView",
    "TreatmentPlanListCreateView",
]
