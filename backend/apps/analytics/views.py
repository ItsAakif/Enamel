"""Analytics API views (T133).

URL layout (mounted under ``/api/v1/``):

* ``GET  /analytics/overview/``     -- dashboard headline metrics + series
* ``GET  /analytics/appointments/`` -- counts, rates, utilisation, funnel, series
* ``GET  /analytics/revenue/``      -- invoiced / collected / outstanding by currency
* ``GET  /analytics/recall/``       -- recall due/completed + reminder delivery
* ``GET  /analytics/waitlist/``     -- offers / claims / expiries
* ``POST /analytics/exports/``      -- materialise a durable export
* ``GET  /analytics/exports/{id}/`` -- status + signed short-lived download URL
* ``GET  /analytics/exports/{id}/download/`` -- the materialised bytes

Every report takes ``?from=&to=&location=`` (two clinic-local dates, inclusive
start / exclusive end). Views hold no query and no metric definition: they
resolve the range, resolve the optional location (a foreign one is a 404, never
a 400 that would confirm it exists elsewhere) and hand off to a service.

All endpoints require ``analytics.view``. The export endpoint additionally
carries the ``export`` throttle (5/hour/user) and audits every request.
"""

from __future__ import annotations

from django.http import HttpResponse
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clinics.models import Location
from apps.core.errors import ApiError
from apps.core.permissions import HasClinicPermission
from apps.core.throttles import ExportThrottle
from apps.scheduling.resources import parse_date_param

from .models import ExportJob
from .serializers import (
    AppointmentsSerializer,
    ExportJobSerializer,
    ExportRequestSerializer,
    OverviewSerializer,
    RecallSerializer,
    RevenueSerializer,
    WaitlistSerializer,
)
from .services import appointments, export, overview, recall, revenue, waitlist

RANGE_PARAMETERS = [
    OpenApiParameter(
        name="from", type=str, location=OpenApiParameter.QUERY, required=True,
        description="Inclusive clinic-local start date (YYYY-MM-DD).",
    ),
    OpenApiParameter(
        name="to", type=str, location=OpenApiParameter.QUERY, required=True,
        description="Inclusive clinic-local end date (YYYY-MM-DD).",
    ),
    OpenApiParameter(
        name="location", type=str, location=OpenApiParameter.QUERY, required=False,
        description="Restrict appointment metrics to one location.",
    ),
]


class NoStoreMixin:
    """Report responses carry clinic figures and must never be cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class AnalyticsScopedView(NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]
    allowed_query_params = {"from", "to", "location"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("analytics.view",)

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

    def resolve_request_range(self, request):
        """Return ``(from_date, to_date, location)`` or raise a 400/404."""

        from_date = parse_date_param(request.query_params.get("from"), "from")
        to_date = parse_date_param(request.query_params.get("to"), "to")
        if to_date < from_date:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"from": ["must not be after `to`"]},
            )
        location = None
        raw_location = (request.query_params.get("location") or "").strip()
        if raw_location:
            location = Location.objects.filter(pk=raw_location).first()
            if location is None:
                raise ApiError("not_found", "Not found.", status_code=404)
        return from_date, to_date, location


class ReportView(AnalyticsScopedView):
    """Shared GET handler; subclasses name the service function."""

    service = None  # type: ignore[assignment]

    @extend_schema(parameters=RANGE_PARAMETERS)
    def get(self, request):
        clinic = self.clinic(request)
        from_date, to_date, location = self.resolve_request_range(request)
        payload = self.service(clinic, from_date, to_date, location)
        return Response(payload)


class OverviewView(ReportView):
    service = staticmethod(overview.report)

    @extend_schema(parameters=RANGE_PARAMETERS, responses=OverviewSerializer)
    def get(self, request):
        return super().get(request)


class AppointmentsView(ReportView):
    service = staticmethod(appointments.report)

    @extend_schema(parameters=RANGE_PARAMETERS, responses=AppointmentsSerializer)
    def get(self, request):
        return super().get(request)


class RevenueView(ReportView):
    service = staticmethod(revenue.report)

    @extend_schema(parameters=RANGE_PARAMETERS, responses=RevenueSerializer)
    def get(self, request):
        return super().get(request)


class RecallView(ReportView):
    service = staticmethod(recall.report)

    @extend_schema(parameters=RANGE_PARAMETERS, responses=RecallSerializer)
    def get(self, request):
        return super().get(request)


class WaitlistView(ReportView):
    service = staticmethod(waitlist.report)

    @extend_schema(parameters=RANGE_PARAMETERS, responses=WaitlistSerializer)
    def get(self, request):
        return super().get(request)


class ExportListCreateView(AnalyticsScopedView):
    allowed_query_params = {"page", "page_size"}
    throttle_classes = [ExportThrottle]

    @extend_schema(request=ExportRequestSerializer, responses={201: ExportJobSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ExportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        location = None
        raw_location = data.get("location")
        if raw_location:
            location = Location.objects.filter(pk=raw_location).first()
            if location is None:
                raise ApiError("not_found", "Not found.", status_code=404)

        date_range = data["range"]
        job = export.create_export(
            clinic,
            report=data["report"],
            export_format=data["format"],
            from_date=date_range["from"],
            to_date=date_range["to"],
            location=location,
            actor=request.user,
            request=request,
        )
        return Response(
            ExportJobSerializer(job).data, status=status.HTTP_201_CREATED
        )


class ExportDetailView(AnalyticsScopedView):
    allowed_query_params: set[str] = set()

    def _get(self, request, export_id) -> ExportJob:
        job = ExportJob.objects.filter(pk=export_id).first()
        if job is None:
            raise ApiError("not_found", "Not found.", status_code=404)
        return job

    @extend_schema(responses=ExportJobSerializer)
    def get(self, request, export_id):
        self.clinic(request)
        return Response(ExportJobSerializer(self._get(request, export_id)).data)


class ExportDownloadView(AnalyticsScopedView):
    allowed_query_params = {"token"}

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="token",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Signed, short-lived download token from the export detail.",
            )
        ],
        responses={
            200: OpenApiResponse(
                description="The materialised export bytes (text/csv or application/json)."
            )
        },
    )
    def get(self, request, export_id):
        self.clinic(request)
        job = ExportJob.objects.filter(pk=export_id).first()
        if job is None:
            raise ApiError("not_found", "Not found.", status_code=404)

        token = (request.query_params.get("token") or "").strip()
        if not export.verify_download_token(token, job=job):
            raise ApiError(
                "permission_denied",
                "You do not have permission to perform this action.",
                status_code=403,
            )
        if job.status != ExportJob.Status.COMPLETED:
            raise ApiError(
                "conflict",
                "The export is not ready.",
                status_code=409,
            )

        content_type, body = export.render_job(job)
        response = HttpResponse(body, content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="{job.reference}.{job.format}"'
        )
        response["Cache-Control"] = "no-store"
        return response


__all__ = [
    "AppointmentsView",
    "ExportDetailView",
    "ExportDownloadView",
    "ExportListCreateView",
    "OverviewView",
    "RecallView",
    "RevenueView",
    "WaitlistView",
]
