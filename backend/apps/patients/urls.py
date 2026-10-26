"""Patient URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    AllergyDetailView,
    AllergyListCreateView,
    EncounterDetailView,
    EncounterListCreateView,
    MedicalHistoryDetailView,
    MedicalHistoryListCreateView,
    PatientDetailView,
    PatientExportView,
    PatientListCreateView,
    PatientTimelineView,
)

app_name = "patients"

urlpatterns = [
    path("patients/", PatientListCreateView.as_view(), name="patient-list"),
    path("patients/<uuid:patient_id>/", PatientDetailView.as_view(), name="patient-detail"),
    path(
        "patients/<uuid:patient_id>/allergies/",
        AllergyListCreateView.as_view(),
        name="allergy-list",
    ),
    path(
        "patients/<uuid:patient_id>/allergies/<uuid:allergy_id>/",
        AllergyDetailView.as_view(),
        name="allergy-detail",
    ),
    path(
        "patients/<uuid:patient_id>/medical-history/",
        MedicalHistoryListCreateView.as_view(),
        name="medical-history-list",
    ),
    path(
        "patients/<uuid:patient_id>/medical-history/<uuid:history_id>/",
        MedicalHistoryDetailView.as_view(),
        name="medical-history-detail",
    ),
    path(
        "patients/<uuid:patient_id>/encounters/",
        EncounterListCreateView.as_view(),
        name="encounter-list",
    ),
    path(
        "patients/<uuid:patient_id>/encounters/<uuid:encounter_id>/",
        EncounterDetailView.as_view(),
        name="encounter-detail",
    ),
    path(
        "patients/<uuid:patient_id>/timeline/",
        PatientTimelineView.as_view(),
        name="patient-timeline",
    ),
    path(
        "patients/<uuid:patient_id>/export/",
        PatientExportView.as_view(),
        name="patient-export",
    ),
]
