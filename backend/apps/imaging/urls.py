"""Imaging URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .api.dicomweb import QidoSeriesListView, QidoStudyListView, WadoInstanceView
from .views import (
    InstancePreviewView,
    StudyDetailView,
    StudyInferenceView,
    StudyListCreateView,
)

app_name = "imaging"

urlpatterns = [
    path("imaging/studies/", StudyListCreateView.as_view(), name="study-list"),
    path(
        "imaging/studies/<uuid:study_id>/",
        StudyDetailView.as_view(),
        name="study-detail",
    ),
    path(
        "imaging/studies/<uuid:study_id>/inference/",
        StudyInferenceView.as_view(),
        name="study-inference",
    ),
    path(
        "imaging/instances/<uuid:instance_id>/preview/",
        InstancePreviewView.as_view(),
        name="instance-preview",
    ),
    path(
        "imaging/dicomweb/studies/",
        QidoStudyListView.as_view(),
        name="dicomweb-studies",
    ),
    path(
        "imaging/dicomweb/studies/<str:study_uid>/series/",
        QidoSeriesListView.as_view(),
        name="dicomweb-series",
    ),
    path(
        "imaging/dicomweb/instances/<str:sop_uid>/",
        WadoInstanceView.as_view(),
        name="dicomweb-instance",
    ),
]
