"""Marketing API views (T132).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /segments/`` -- list / create a non-clinical segment
* ``POST /segments/preview/`` -- count + sample for an unsaved predicate
* ``GET /segments/{id}/preview/`` -- count + sample for a stored segment
* ``GET|POST /campaigns/`` -- list / create a campaign
* ``POST /campaigns/{id}/approve/`` -- record an approval (``marketing.approve``)
* ``POST /campaigns/{id}/activate/`` -- create recipients and send
* ``POST /campaigns/{id}/pause/`` -- stop further sends
* ``GET /campaigns/{id}/metrics/`` -- counted delivery metrics

Every staff lookup runs through the tenant-scoped manager, so a foreign id is an
indistinguishable 404. Responses are ``Cache-Control: no-store``.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission

from .models import Campaign, Segment
from .serializers import (
    CampaignMetricsSerializer,
    CampaignSerializer,
    SegmentPreviewRequestSerializer,
    SegmentSerializer,
)
from .services import campaigns as campaign_service
from .services import metrics as metrics_service
from .services import segments as segment_service


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class MarketingScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("marketing.view",)

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

    def segment(self, request, segment_id) -> Segment:
        segment = Segment.objects.filter(clinic=self.clinic(request), pk=segment_id).first()
        if segment is None:
            raise _not_found()
        return segment

    def campaign(self, request, campaign_id) -> Campaign:
        campaign = (
            Campaign.objects.filter(clinic=self.clinic(request), pk=campaign_id)
            .select_related("segment", "template")
            .first()
        )
        if campaign is None:
            raise _not_found()
        return campaign


# --------------------------------------------------------------------------- #
# Segments
# --------------------------------------------------------------------------- #


class SegmentListCreateView(MarketingScopedView):
    allowed_query_params = {"page", "page_size", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("marketing.view",)
        return ("marketing.manage",)

    @extend_schema(responses=SegmentSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Segment.objects.filter(clinic=clinic).order_by("name", "id")
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SegmentSerializer(page, many=True).data)

    @extend_schema(request=SegmentSerializer, responses={201: SegmentSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = SegmentSerializer(
            data=request.data, context={"clinic": clinic, "user": request.user}
        )
        serializer.is_valid(raise_exception=True)
        segment = serializer.save()
        record_audit(
            "marketing.segment_created",
            entity=segment,
            after={
                "name": segment.name,
                "predicate_json": segment.predicate_json,
                "is_active": segment.is_active,
            },
            actor=request.user,
            request=request,
        )
        return Response(SegmentSerializer(segment).data, status=status.HTTP_201_CREATED)


class SegmentPreviewView(MarketingScopedView):
    allowed_query_params: set[str] = set()

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("marketing.manage",)

    @extend_schema(request=SegmentPreviewRequestSerializer, responses=dict)
    def post(self, request):
        clinic = self.clinic(request)
        serializer = SegmentPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(
            segment_service.preview(clinic, serializer.validated_data["predicate_json"])
        )


class SegmentDetailPreviewView(MarketingScopedView):
    allowed_query_params: set[str] = set()

    @extend_schema(responses=dict)
    def get(self, request, segment_id):
        segment = self.segment(request, segment_id)
        return Response(segment_service.preview(segment.clinic, segment.predicate_json))


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #


class CampaignListCreateView(MarketingScopedView):
    allowed_query_params = {"page", "page_size", "status", "segment"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("marketing.view",)
        return ("marketing.manage",)

    @extend_schema(responses=CampaignSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = (
            Campaign.objects.filter(clinic=clinic)
            .select_related("segment", "template")
            .order_by("-created_at", "-id")
        )
        for field in ("status", "segment"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(CampaignSerializer(page, many=True).data)

    @extend_schema(request=CampaignSerializer, responses={201: CampaignSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = CampaignSerializer(
            data=request.data, context={"clinic": clinic, "user": request.user}
        )
        serializer.is_valid(raise_exception=True)
        campaign = serializer.save()
        record_audit(
            "marketing.campaign_created",
            entity=campaign,
            after=campaign_service.snapshot(campaign),
            actor=request.user,
            request=request,
        )
        return Response(CampaignSerializer(campaign).data, status=status.HTTP_201_CREATED)


class CampaignApproveView(MarketingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("marketing.approve",)

    @extend_schema(request=None, responses=CampaignSerializer)
    def post(self, request, campaign_id):
        campaign = self.campaign(request, campaign_id)
        campaign = campaign_service.approve(
            campaign, actor=request.user, request=request
        )
        return Response(CampaignSerializer(campaign).data)


class CampaignActivateView(MarketingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("marketing.approve",)

    @extend_schema(request=None, responses=CampaignSerializer)
    def post(self, request, campaign_id):
        campaign = self.campaign(request, campaign_id)
        campaign = campaign_service.activate(
            campaign, actor=request.user, request=request
        )
        return Response(CampaignSerializer(campaign).data)


class CampaignPauseView(MarketingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("marketing.manage",)

    @extend_schema(request=None, responses=CampaignSerializer)
    def post(self, request, campaign_id):
        campaign = self.campaign(request, campaign_id)
        campaign = campaign_service.pause(campaign, actor=request.user, request=request)
        return Response(CampaignSerializer(campaign).data)


class CampaignMetricsView(MarketingScopedView):
    allowed_query_params: set[str] = set()

    @extend_schema(responses=CampaignMetricsSerializer)
    def get(self, request, campaign_id):
        campaign = self.campaign(request, campaign_id)
        return Response(metrics_service.report(campaign))


__all__ = [
    "CampaignActivateView",
    "CampaignApproveView",
    "CampaignListCreateView",
    "CampaignMetricsView",
    "CampaignPauseView",
    "SegmentDetailPreviewView",
    "SegmentListCreateView",
    "SegmentPreviewView",
]
