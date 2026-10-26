"""Odontogram API views (T108).

URL layout (mounted under ``/api/v1/``):

* ``GET  /patients/{id}/chart/``            full chart (active states only)
* ``POST /patients/{id}/chart/batch/``      atomic batch save
* ``GET  /patients/{id}/chart/history/``    append-only history (cursor)
* ``POST /chart/states/{id}/transition/``   status transition

Every object is looked up *inside* ``request.clinic`` (the tenant manager is
scoped by ``ClinicScopeMiddleware``), so a foreign patient or state is an
indistinguishable 404. Reads require ``charting.view``; writes require
``charting.edit``. Responses are ``Cache-Control: no-store`` because a chart is
PHI.
"""

from __future__ import annotations

from urllib.parse import urlencode

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import ApiError
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient

from .models import ToothState
from .serializers import (
    ChartBatchRequestSerializer,
    ChartBatchResponseSerializer,
    ChartHistoryPageSerializer,
    ChartResponseSerializer,
    ChartStateTransitionSerializer,
    ToothStateSerializer,
)
from .services.batch import apply_batch
from .services.chart import build_chart, state_payload, transition_state
from .services.history import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    event_payload,
    page_history,
)

DENTITION_PARAMETER = OpenApiParameter(
    name="dentition",
    type=str,
    location=OpenApiParameter.QUERY,
    required=False,
    enum=["primary", "mixed", "permanent"],
    description="Read-only override of the derived patient dentition.",
)


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


class NoStoreMixin:
    """Chart responses carry PHI and must never be cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class ChartScopedView(NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("charting.view",)
        return ("charting.edit",)


def _get_patient(request, patient_id, *, for_write: bool = False) -> Patient:
    """Resolve a patient inside the request clinic.

    A deleted record is hidden from reads (404, indistinguishable from a foreign
    id). A write against an archived *or* deleted record is 403
    ``patient_archived``: the caller can see the record, it is simply read-only.
    """

    patient = Patient.objects.filter(pk=patient_id).first()
    if patient is None:
        raise _not_found()
    if for_write:
        if patient.is_archived:
            raise ApiError(
                "patient_archived",
                "This patient record is archived and cannot be modified.",
                status_code=403,
            )
    elif patient.deleted_at is not None:
        raise _not_found()
    return patient


class PatientChartView(ChartScopedView):
    allowed_query_params = {"dentition"}

    @extend_schema(parameters=[DENTITION_PARAMETER], responses=ChartResponseSerializer)
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        dentition = (request.query_params.get("dentition") or "").strip() or None
        return Response(build_chart(patient, dentition=dentition))


class PatientChartBatchView(ChartScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("charting.edit",)

    @extend_schema(request=ChartBatchRequestSerializer, responses=ChartBatchResponseSerializer)
    def post(self, request, patient_id):
        serializer = ChartBatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = _get_patient(request, patient_id, for_write=True)
        result = apply_batch(
            patient,
            changes=list(data["changes"]),
            idempotency_key=data["idempotency_key"],
            dentition=data.get("dentition"),
            force=bool(data.get("force", False)),
            actor=request.user,
            request=request,
        )
        return Response(result, status=status.HTTP_200_OK)


class PatientChartHistoryView(ChartScopedView):
    allowed_query_params = {"tooth", "cursor", "page_size"}

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="tooth",
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
            OpenApiParameter(
                name="page_size",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
            ),
        ],
        responses=ChartHistoryPageSerializer,
    )
    def get(self, request, patient_id):
        patient = _get_patient(request, patient_id)
        tooth = self._tooth(request)
        page_size = self._page_size(request)
        cursor = (request.query_params.get("cursor") or "").strip() or None

        try:
            page, next_cursor = page_history(
                patient, tooth=tooth, cursor=cursor, page_size=page_size
            )
        except ValueError as exc:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"cursor": [str(exc)]},
            ) from exc

        next_url = None
        if next_cursor:
            params = {"cursor": next_cursor, "page_size": page_size}
            if tooth is not None:
                params["tooth"] = tooth
            next_url = request.build_absolute_uri(f"{request.path}?{urlencode(params)}")

        return Response(
            {
                "next": next_url,
                "previous": None,
                "results": [event_payload(event) for event in page],
            }
        )

    @staticmethod
    def _tooth(request) -> int | None:
        raw = request.query_params.get("tooth")
        if raw in (None, ""):
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"tooth": ["enter a valid FDI tooth code"]},
            ) from None

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


class ChartStateTransitionView(ChartScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("charting.edit",)

    @extend_schema(request=ChartStateTransitionSerializer, responses=ToothStateSerializer)
    def post(self, request, state_id):
        state = (
            ToothState.objects.select_related("patient").filter(pk=state_id).first()
        )
        if state is None:
            raise _not_found()
        patient = state.patient
        if patient.is_archived:
            raise ApiError(
                "patient_archived",
                "This patient record is archived and cannot be modified.",
                status_code=403,
            )

        serializer = ChartStateTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        updated = transition_state(
            state,
            to_status=data["to_status"],
            plan_item_id=data.get("plan_item_id"),
            actor=request.user,
            request=request,
        )
        return Response(state_payload(updated))


__all__ = [
    "ChartScopedView",
    "ChartStateTransitionView",
    "NoStoreMixin",
    "PatientChartBatchView",
    "PatientChartHistoryView",
    "PatientChartView",
]
