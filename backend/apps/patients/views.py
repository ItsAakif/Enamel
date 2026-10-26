"""Patient, allergy, medical-history, encounter, timeline and export views (T106).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /patients/``
* ``GET|PATCH|DELETE /patients/{id}/``
* ``GET|POST /patients/{id}/allergies/``, ``PATCH /patients/{id}/allergies/{allergy_id}/``
* ``GET|POST /patients/{id}/medical-history/``, ``PATCH /patients/{id}/medical-history/{history_id}/``
* ``GET|POST /patients/{id}/encounters/``, ``GET /patients/{id}/encounters/{encounter_id}/``
* ``GET /patients/{id}/timeline/`` (cursor paginated)
* ``GET /patients/{id}/export/?format=json|pdf``

Every view resolves the clinic from the ``X-Clinic-Id`` header (or the single
active membership) through ``ClinicScopeMiddleware`` and looks objects up
*inside* that clinic, so a foreign patient is an indistinguishable 404. All
responses are ``Cache-Control: no-store`` because they carry PHI.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.core.throttles import ExportThrottle

from .filters import apply_patient_filters, apply_patient_ordering
from .models import Allergy, Encounter, MedicalHistory, Patient
from .serializers import (
    AllergySerializer,
    AllergyWriteSerializer,
    EncounterSerializer,
    EncounterWriteSerializer,
    MedicalHistorySerializer,
    MedicalHistoryWriteSerializer,
    PatientDetailSerializer,
    PatientListSerializer,
    PatientWriteSerializer,
    chart_summary,
)
from .services.dedup import find_duplicates
from .services.encounters import create_encounter
from .services.export import build_patient_export, build_patient_export_pdf
from .services.history import (
    patient_allergies,
    patient_medical_history,
    record_allergy,
    record_medical_history,
    summarize_allergies,
    summarize_medical_history,
    update_allergy,
    update_medical_history,
)
from .services.patients import archive_patient, create_patient, update_patient
from .services.timeline import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    build_timeline,
    paginate_timeline,
)

PATIENT_LIST_PARAMETERS = [
    OpenApiParameter(name="search", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="source", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="location", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="ordering", type=str, location=OpenApiParameter.QUERY, required=False),
]

class TimelineEventSerializer(serializers.Serializer):
    """Read-only projection of a timeline event (OpenAPI only)."""

    type = serializers.CharField()
    at = serializers.DateTimeField()
    title = serializers.CharField()
    summary = serializers.CharField()
    entity_id = serializers.CharField()
    metadata = serializers.DictField()


TIMELINE_PAGE_SCHEMA = inline_serializer(
    name="PatientTimelinePage",
    fields={
        "next": serializers.CharField(allow_null=True),
        "previous": serializers.CharField(allow_null=True),
        "results": TimelineEventSerializer(many=True),
    },
)

PATIENT_EXPORT_SCHEMA = inline_serializer(
    name="PatientExport",
    fields={
        "export": serializers.DictField(),
        "clinic": serializers.DictField(),
        "patient": serializers.DictField(),
        "allergies": serializers.ListField(child=serializers.DictField()),
        "medical_history": serializers.ListField(child=serializers.DictField()),
        "encounters": serializers.ListField(child=serializers.DictField()),
    },
)


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _require_clinic(request):
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


def _get_patient(request, patient_id, *, include_deleted: bool = False) -> Patient:
    queryset = Patient.objects.select_related("clinic", "primary_location", "primary_provider")
    if not include_deleted:
        queryset = queryset.filter(deleted_at__isnull=True)
    patient = queryset.filter(pk=patient_id).first()
    if patient is None:
        raise _not_found()
    return patient


def _assert_editable(patient: Patient) -> None:
    if patient.is_archived:
        raise ApiError(
            "patient_archived",
            "This patient record is archived and cannot be modified.",
            status_code=409,
        )


def _patient_detail_payload(patient: Patient) -> dict:
    allergies = patient_allergies(patient)
    history = patient_medical_history(patient)
    payload = dict(PatientDetailSerializer(patient).data)
    payload["allergies"] = AllergySerializer(allergies, many=True).data
    payload["allergy_summary"] = summarize_allergies(allergies)
    payload["medical_history"] = MedicalHistorySerializer(history, many=True).data
    payload["medical_history_summary"] = summarize_medical_history(history)
    payload["chart_summary"] = chart_summary(patient)
    return payload


class ExportContentNegotiation(DefaultContentNegotiation):
    """Treat ``?format=`` as the export payload selector, not a renderer.

    DRF reserves ``?format=`` for renderer overrides (``?format=json``); with
    ``?format=pdf`` it would look for a renderer named ``pdf`` and 404 before
    the view runs. The export endpoint owns that parameter, so renderer
    selection falls back to the ``Accept`` header.
    """

    def filter_renderers(self, renderers, format):
        return renderers


class NoStoreMixin:
    """Every patient response is uncacheable: it contains PHI."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class PatientScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("patients.view",)


class PatientListCreateView(PatientScopedView):
    allowed_query_params = {
        "page",
        "page_size",
        "ordering",
        "search",
        "status",
        "source",
        "location",
        "provider",
    }

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("patients.view",)
        return ("patients.edit",)

    @extend_schema(parameters=PATIENT_LIST_PARAMETERS, responses=PatientListSerializer(many=True))
    def get(self, request):
        clinic = _require_clinic(request)
        queryset = Patient.objects.filter(clinic=clinic)
        queryset = apply_patient_filters(queryset, request.query_params)
        queryset = apply_patient_ordering(queryset, request.query_params)
        queryset = queryset.select_related("primary_location", "primary_provider")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = PatientListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        request=PatientWriteSerializer,
        responses={201: PatientDetailSerializer},
    )
    def post(self, request):
        clinic = _require_clinic(request)
        serializer = PatientWriteSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        fields = serializer.to_model_fields()
        override = bool(serializer.validated_data.get("override_duplicates", False))

        candidates = find_duplicates(
            clinic,
            first_name=fields.get("first_name", ""),
            last_name=fields.get("last_name", ""),
            date_of_birth=fields.get("date_of_birth"),
            phone_e164=fields.get("phone_e164", ""),
            email=fields.get("email", ""),
        )
        if candidates and not override:
            raise ApiError(
                "duplicate_candidates",
                "A patient with these details may already exist.",
                status_code=409,
                details={"candidates": [candidate.as_payload() for candidate in candidates]},
            )

        with transaction.atomic():
            patient = create_patient(
                clinic,
                fields=fields,
                actor=request.user,
                request=request,
            )
            if candidates and override:
                record_audit(
                    "patient.duplicate_overridden",
                    entity=patient,
                    after={
                        "override": True,
                        "candidate_ids": [candidate.id for candidate in candidates],
                    },
                    actor=request.user,
                    request=request,
                )

        payload = _patient_detail_payload(patient)
        payload["duplicate_candidates"] = [candidate.as_payload() for candidate in candidates]
        return Response(payload, status=status.HTTP_201_CREATED)


class PatientDetailView(PatientScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("patients.view",)
        if request.method == "PATCH":
            return ("patients.edit",)
        return ("patients.delete",)

    @extend_schema(responses=PatientDetailSerializer)
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        return Response(_patient_detail_payload(patient))

    @extend_schema(request=PatientWriteSerializer, responses=PatientDetailSerializer)
    def patch(self, request, patient_id):
        patient = _get_patient(request, patient_id, include_deleted=True)
        _assert_editable(patient)

        serializer = PatientWriteSerializer(
            data=request.data,
            context={"clinic": patient.clinic},
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        fields = serializer.to_model_fields()
        if fields:
            update_patient(patient, fields=fields, actor=request.user, request=request)
        return Response(_patient_detail_payload(patient))

    @extend_schema(responses={204: None})
    def delete(self, request, patient_id):
        patient = _get_patient(request, patient_id, include_deleted=True)
        if patient.deleted_at is not None:
            raise ApiError(
                "patient_archived",
                "This patient record is archived and cannot be modified.",
                status_code=409,
            )
        archive_patient(patient, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AllergyListCreateView(PatientScopedView):
    allowed_query_params = {"page", "page_size", "active", "ordering"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("clinical.history.view",)
        return ("clinical.history.edit",)

    @extend_schema(responses=AllergySerializer(many=True))
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        active_only = (request.query_params.get("active") or "").lower() in {"1", "true"}
        rows = patient_allergies(patient, active_only=active_only)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        serializer = AllergySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(request=AllergyWriteSerializer, responses={201: AllergySerializer})
    def post(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        _assert_editable(patient)
        serializer = AllergyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allergy = record_allergy(
            patient,
            fields=dict(serializer.validated_data),
            actor=request.user,
            request=request,
        )
        return Response(AllergySerializer(allergy).data, status=status.HTTP_201_CREATED)


class AllergyDetailView(PatientScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("clinical.history.edit",)

    def _get_allergy(self, request, patient_id, allergy_id) -> tuple[Patient, Allergy]:
        patient = _get_patient(request, patient_id)
        allergy = Allergy.objects.filter(pk=allergy_id, patient=patient).first()
        if allergy is None:
            raise _not_found()
        return patient, allergy

    @extend_schema(request=AllergyWriteSerializer, responses=AllergySerializer)
    def patch(self, request, patient_id, allergy_id):
        patient, allergy = self._get_allergy(request, patient_id, allergy_id)
        _assert_editable(patient)
        serializer = AllergyWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        if fields:
            update_allergy(allergy, fields=fields, actor=request.user, request=request)
        return Response(AllergySerializer(allergy).data)


class MedicalHistoryListCreateView(PatientScopedView):
    allowed_query_params = {"page", "page_size", "active", "ordering", "kind"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("clinical.history.view",)
        return ("clinical.history.edit",)

    @extend_schema(responses=MedicalHistorySerializer(many=True))
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        active_only = (request.query_params.get("active") or "").lower() in {"1", "true"}
        rows = patient_medical_history(patient, active_only=active_only)
        kind = (request.query_params.get("kind") or "").strip()
        if kind:
            rows = [row for row in rows if row.kind == kind]
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        serializer = MedicalHistorySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        request=MedicalHistoryWriteSerializer,
        responses={201: MedicalHistorySerializer},
    )
    def post(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        _assert_editable(patient)
        serializer = MedicalHistoryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = record_medical_history(
            patient,
            fields=dict(serializer.validated_data),
            actor=request.user,
            request=request,
        )
        return Response(MedicalHistorySerializer(entry).data, status=status.HTTP_201_CREATED)


class MedicalHistoryDetailView(PatientScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("clinical.history.edit",)

    def _get_entry(self, request, patient_id, history_id) -> tuple[Patient, MedicalHistory]:
        patient = _get_patient(request, patient_id)
        entry = MedicalHistory.objects.filter(pk=history_id, patient=patient).first()
        if entry is None:
            raise _not_found()
        return patient, entry

    @extend_schema(
        request=MedicalHistoryWriteSerializer,
        responses=MedicalHistorySerializer,
    )
    def patch(self, request, patient_id, history_id):
        patient, entry = self._get_entry(request, patient_id, history_id)
        _assert_editable(patient)
        serializer = MedicalHistoryWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        if fields:
            update_medical_history(entry, fields=fields, actor=request.user, request=request)
        return Response(MedicalHistorySerializer(entry).data)


class EncounterListCreateView(PatientScopedView):
    allowed_query_params = {"page", "page_size", "kind", "status", "ordering"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            # The registry has no ``encounters.view``; the clinical read
            # permission is the documented read gate (assistant is read-only).
            return ("clinical.history.view",)
        return ("encounters.edit",)

    @extend_schema(responses=EncounterSerializer(many=True))
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        queryset = Encounter.objects.filter(patient=patient)
        kind = (request.query_params.get("kind") or "").strip()
        if kind:
            queryset = queryset.filter(kind=kind)
        encounter_status = (request.query_params.get("status") or "").strip()
        if encounter_status:
            queryset = queryset.filter(status=encounter_status)
        queryset = queryset.order_by("-occurred_at", "-id")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = EncounterSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(request=EncounterWriteSerializer, responses={201: EncounterSerializer})
    def post(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        _assert_editable(patient)
        serializer = EncounterWriteSerializer(
            data=request.data,
            context={"clinic": patient.clinic},
        )
        serializer.is_valid(raise_exception=True)
        encounter = create_encounter(
            patient,
            fields=serializer.to_model_fields(),
            actor=request.user,
            request=request,
        )
        return Response(EncounterSerializer(encounter).data, status=status.HTTP_201_CREATED)


class EncounterDetailView(PatientScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("clinical.history.view",)

    @extend_schema(responses=EncounterSerializer)
    def get(self, request, patient_id, encounter_id):
        patient = _get_patient(request, patient_id)
        encounter = Encounter.objects.filter(pk=encounter_id, patient=patient).first()
        if encounter is None:
            raise _not_found()
        return Response(EncounterSerializer(encounter).data)


class PatientTimelineView(PatientScopedView):
    allowed_query_params = {"page_size", "cursor"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("patients.view",)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="page_size",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
            OpenApiParameter(
                name="cursor",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses={200: TIMELINE_PAGE_SCHEMA},
    )
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        page_size = self._page_size(request)
        cursor = (request.query_params.get("cursor") or "").strip() or None

        events = build_timeline(patient)
        try:
            page, next_cursor = paginate_timeline(events, cursor=cursor, page_size=page_size)
        except ValueError as exc:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"cursor": [str(exc)]},
            ) from exc

        next_url = None
        if next_cursor:
            query = urlencode({"cursor": next_cursor, "page_size": page_size})
            next_url = request.build_absolute_uri(f"{request.path}?{query}")

        return Response(
            {
                "next": next_url,
                "previous": None,
                "results": [event.as_payload() for event in page],
            }
        )

    @staticmethod
    def _page_size(request) -> int:
        raw = request.query_params.get("page_size")
        if raw in (None, ""):
            return DEFAULT_PAGE_SIZE
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value < 1 or value > MAX_PAGE_SIZE:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"page_size": [f"enter a value between 1 and {MAX_PAGE_SIZE}"]},
            )
        return value


class PatientExportView(PatientScopedView):
    """One patient, one clinic, capped rows, audited and throttled."""

    allowed_query_params = {"format"}
    throttle_classes = [ExportThrottle]
    content_negotiation_class = ExportContentNegotiation

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("patients.export",)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="format",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                enum=["json", "pdf"],
            )
        ],
        responses={200: PATIENT_EXPORT_SCHEMA},
    )
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        export_format = (request.query_params.get("format") or "json").strip().lower()
        if export_format not in {"json", "pdf"}:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"format": ["select one of: json, pdf"]},
            )

        if export_format == "pdf":
            payload_bytes, rows = build_patient_export_pdf(patient)
            self._audit(request, patient, "pdf", rows)
            response = HttpResponse(payload_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{patient.number}.pdf"'
            return response

        payload, rows = build_patient_export(patient)
        self._audit(request, patient, "json", rows)
        return Response(payload)

    @staticmethod
    def _audit(request, patient: Patient, export_format: str, rows: int) -> None:
        record_audit(
            "patient.exported",
            entity=patient,
            after={
                "format": export_format,
                "rows": rows,
                "exported_at": timezone.now(),
            },
            actor=request.user,
            request=request,
        )


__all__ = [
    "AllergyDetailView",
    "AllergyListCreateView",
    "EncounterDetailView",
    "EncounterListCreateView",
    "MedicalHistoryDetailView",
    "MedicalHistoryListCreateView",
    "PatientDetailView",
    "PatientExportView",
    "PatientListCreateView",
    "PatientTimelineView",
]
