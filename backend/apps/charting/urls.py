"""Charting URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    ChartStateTransitionView,
    PatientChartBatchView,
    PatientChartHistoryView,
    PatientChartView,
)

app_name = "charting"

urlpatterns = [
    path(
        "patients/<uuid:patient_id>/chart/",
        PatientChartView.as_view(),
        name="patient-chart",
    ),
    path(
        "patients/<uuid:patient_id>/chart/batch/",
        PatientChartBatchView.as_view(),
        name="patient-chart-batch",
    ),
    path(
        "patients/<uuid:patient_id>/chart/history/",
        PatientChartHistoryView.as_view(),
        name="patient-chart-history",
    ),
    path(
        "chart/states/<uuid:state_id>/transition/",
        ChartStateTransitionView.as_view(),
        name="chart-state-transition",
    ),
]
