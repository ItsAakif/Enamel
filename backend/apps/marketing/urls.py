"""Marketing URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    CampaignActivateView,
    CampaignApproveView,
    CampaignListCreateView,
    CampaignMetricsView,
    CampaignPauseView,
    SegmentDetailPreviewView,
    SegmentListCreateView,
    SegmentPreviewView,
)

app_name = "marketing"

urlpatterns = [
    path("segments/", SegmentListCreateView.as_view(), name="segment-list"),
    path("segments/preview/", SegmentPreviewView.as_view(), name="segment-preview"),
    path(
        "segments/<uuid:segment_id>/preview/",
        SegmentDetailPreviewView.as_view(),
        name="segment-detail-preview",
    ),
    path("campaigns/", CampaignListCreateView.as_view(), name="campaign-list"),
    path(
        "campaigns/<uuid:campaign_id>/approve/",
        CampaignApproveView.as_view(),
        name="campaign-approve",
    ),
    path(
        "campaigns/<uuid:campaign_id>/activate/",
        CampaignActivateView.as_view(),
        name="campaign-activate",
    ),
    path(
        "campaigns/<uuid:campaign_id>/pause/",
        CampaignPauseView.as_view(),
        name="campaign-pause",
    ),
    path(
        "campaigns/<uuid:campaign_id>/metrics/",
        CampaignMetricsView.as_view(),
        name="campaign-metrics",
    ),
]
