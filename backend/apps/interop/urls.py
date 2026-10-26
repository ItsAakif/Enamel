"""Interop URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .views import (
    ConnectorListCreateView,
    ConnectorRunsView,
    ConnectorSyncView,
    FhirAppointmentView,
    FhirEncounterView,
    FhirPatientView,
)

app_name = "interop"

urlpatterns = [
    path(
        "fhir/Patient/<uuid:patient_id>/",
        FhirPatientView.as_view(),
        name="fhir-patient",
    ),
    path(
        "fhir/Appointment/<uuid:appointment_id>/",
        FhirAppointmentView.as_view(),
        name="fhir-appointment",
    ),
    path(
        "fhir/Encounter/<uuid:encounter_id>/",
        FhirEncounterView.as_view(),
        name="fhir-encounter",
    ),
    path("connectors/", ConnectorListCreateView.as_view(), name="connector-list"),
    path(
        "connectors/<uuid:connector_id>/sync/",
        ConnectorSyncView.as_view(),
        name="connector-sync",
    ),
    path(
        "connectors/<uuid:connector_id>/runs/",
        ConnectorRunsView.as_view(),
        name="connector-runs",
    ),
]
