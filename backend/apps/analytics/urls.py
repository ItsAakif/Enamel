"""Analytics URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .views import (
    AppointmentsView,
    ExportDetailView,
    ExportDownloadView,
    ExportListCreateView,
    OverviewView,
    RecallView,
    RevenueView,
    WaitlistView,
)

app_name = "analytics"

urlpatterns = [
    path("analytics/overview/", OverviewView.as_view(), name="overview"),
    path("analytics/appointments/", AppointmentsView.as_view(), name="appointments"),
    path("analytics/revenue/", RevenueView.as_view(), name="revenue"),
    path("analytics/recall/", RecallView.as_view(), name="recall"),
    path("analytics/waitlist/", WaitlistView.as_view(), name="waitlist"),
    path("analytics/exports/", ExportListCreateView.as_view(), name="export-list"),
    path(
        "analytics/exports/<uuid:export_id>/",
        ExportDetailView.as_view(),
        name="export-detail",
    ),
    path(
        "analytics/exports/<uuid:export_id>/download/",
        ExportDownloadView.as_view(),
        name="export-download",
    ),
]
