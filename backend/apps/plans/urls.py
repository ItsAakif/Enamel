"""Plans URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    ConsentPdfView,
    ConsentRecordListCreateView,
    ConsentTemplateDetailView,
    ConsentTemplateListCreateView,
    DocumentDownloadView,
    DocumentListCreateView,
    TreatmentPlanActionView,
    TreatmentPlanDetailView,
    TreatmentPlanListCreateView,
)

app_name = "plans"

urlpatterns = [
    path(
        "treatment-plans/",
        TreatmentPlanListCreateView.as_view(),
        name="treatment-plan-list",
    ),
    path(
        "treatment-plans/<uuid:plan_id>/",
        TreatmentPlanDetailView.as_view(),
        name="treatment-plan-detail",
    ),
    path(
        "treatment-plans/<uuid:plan_id>/<str:action>/",
        TreatmentPlanActionView.as_view(),
        name="treatment-plan-action",
    ),
    path(
        "consent-templates/",
        ConsentTemplateListCreateView.as_view(),
        name="consent-template-list",
    ),
    path(
        "consent-templates/<uuid:template_id>/",
        ConsentTemplateDetailView.as_view(),
        name="consent-template-detail",
    ),
    # ``messaging`` owns ``/consents/``; plan consents are created here instead.
    path(
        "consent-records/",
        ConsentRecordListCreateView.as_view(),
        name="consent-record-list",
    ),
    path(
        "consents/<uuid:record_id>/pdf/",
        ConsentPdfView.as_view(),
        name="consent-pdf",
    ),
    path("documents/", DocumentListCreateView.as_view(), name="document-list"),
    path(
        "documents/<uuid:document_id>/download/",
        DocumentDownloadView.as_view(),
        name="document-download",
    ),
]
