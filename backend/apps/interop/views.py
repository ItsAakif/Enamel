"""Interop API views (T130).

URL layout (mounted under ``/api/v1/``):

* ``GET /fhir/Patient/{id}/``, ``/fhir/Appointment/{id}/``, ``/fhir/Encounter/{id}/``
* ``GET|POST /connectors/``
* ``POST /connectors/{id}/sync/``
* ``GET /connectors/{id}/runs/``

Permission decision (documented)
--------------------------------
The FHIR facade is read-only and its consumers are analytics/integration
readers, so it requires ``analytics.view``. Connector *configuration* is an
owner-only capability, so the connector endpoints require ``integrations.manage``
(the ``integrations.manage`` **or** ``analytics.view`` choice in the task
specification is split by read vs. configure, which is the least-privilege
reading). Cross-clinic ids are indistinguishable 404s.

Responses are ``application/fhir+json`` for FHIR resources and ``no-store``
everywhere.
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Encounter, Patient
from apps.scheduling.models import Appointment

from .connectors.base import (
    ConnectorNotConfigured,
    validate_base_url,
)
from .fhir.resources import (
    FHIR_MEDIA_TYPE,
    render_appointment,
    render_encounter,
    render_patient,
)
from .models import ConnectorConfig, ConnectorKind, ConnectorMode, SyncRun
from .serializers import (
    ConnectorConfigCreateSerializer,
    ConnectorConfigSerializer,
    SyncRunSerializer,
)
from .services.sync import run_sync


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _not_configured(message: str = "The connector is not configured for live mode.") -> ApiError:
    return ApiError("connector_not_configured", message, status_code=409)


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class FhirScopedView(NoStoreMixin, APIView):
    """Read-only FHIR facade base view."""

    permission_classes = [HasClinicPermission]
    required_perms = ("analytics.view",)

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


class FhirPatientView(FhirScopedView):
    @extend_schema(responses={200: dict}, operation_id="fhir_patient_read")
    def get(self, request, patient_id):
        patient = Patient.objects.filter(clinic=self.clinic(request), pk=patient_id).first()
        if patient is None:
            raise _not_found()
        return Response(render_patient(patient), content_type=FHIR_MEDIA_TYPE)


class FhirAppointmentView(FhirScopedView):
    @extend_schema(responses={200: dict}, operation_id="fhir_appointment_read")
    def get(self, request, appointment_id):
        appointment = (
            Appointment.objects.filter(clinic=self.clinic(request), pk=appointment_id)
            .select_related("patient", "provider", "appointment_type")
            .first()
        )
        if appointment is None:
            raise _not_found()
        return Response(render_appointment(appointment), content_type=FHIR_MEDIA_TYPE)


class FhirEncounterView(FhirScopedView):
    @extend_schema(responses={200: dict}, operation_id="fhir_encounter_read")
    def get(self, request, encounter_id):
        encounter = (
            Encounter.objects.filter(clinic=self.clinic(request), pk=encounter_id)
            .select_related("patient", "provider")
            .first()
        )
        if encounter is None:
            raise _not_found()
        return Response(render_encounter(encounter), content_type=FHIR_MEDIA_TYPE)


class ConnectorScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    """Base view for connector administration (owner-only)."""

    permission_classes = [HasClinicPermission]
    required_perms = ("integrations.manage",)

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

    def _connector(self, request, connector_id) -> ConnectorConfig:
        connector = ConnectorConfig.objects.filter(
            clinic=self.clinic(request), pk=connector_id
        ).first()
        if connector is None:
            raise _not_found()
        return connector


CONNECTOR_SYNC_SCHEMA = inline_serializer(
    name="ConnectorSyncResult",
    fields={
        "mode": serializers.CharField(),
        "run": SyncRunSerializer(),
    },
)


class ConnectorListCreateView(ConnectorScopedView):
    allowed_query_params = {"page", "page_size", "kind", "mode", "is_active"}

    @extend_schema(responses=ConnectorConfigSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = ConnectorConfig.objects.filter(clinic=clinic).order_by("name", "id")
        for field in ("kind", "mode"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ConnectorConfigSerializer(page, many=True).data
        )

    @extend_schema(
        request=ConnectorConfigCreateSerializer,
        responses={201: ConnectorConfigSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ConnectorConfigCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        mode = data.get("mode") or ConnectorMode.SIMULATOR
        credentials = data.get("credentials") or {}
        if mode == ConnectorMode.LIVE:
            if not credentials:
                raise _not_configured("Live connectors require credentials.")
            if not bool(getattr(settings, "CONNECTOR_ALLOW_LIVE", False)):
                raise _not_configured(
                    "Live connectors are disabled (CONNECTOR_ALLOW_LIVE)."
                )
            # SSRF: https, allowlist and non-private resolution.
            validate_base_url(data.get("base_url", ""))

        # Encrypt through the single credential write path.
        from .credentials import encrypt_credentials

        connector = ConnectorConfig(
            clinic=clinic,
            kind=data.get("kind") or ConnectorKind.OPEN_DENTAL,
            name=data["name"],
            mode=mode,
            base_url=data.get("base_url", ""),
            is_active=data.get("is_active", True),
            settings_json=data.get("settings_json") or {},
            credentials_enc=encrypt_credentials(credentials),
        )
        connector.save()
        record_audit(
            "connector.created",
            entity=connector,
            after={
                "kind": connector.kind,
                "mode": connector.mode,
                "is_active": connector.is_active,
                "has_credentials": bool(connector.credentials_enc),
            },
            actor=request.user,
            request=request,
        )
        return Response(
            ConnectorConfigSerializer(connector).data, status=status.HTTP_201_CREATED
        )


class ConnectorSyncView(ConnectorScopedView):
    @extend_schema(request=None, responses={200: CONNECTOR_SYNC_SCHEMA})
    def post(self, request, connector_id):
        connector = self._connector(request, connector_id)
        if not connector.is_active:
            raise ApiError(
                "connector_inactive",
                "This connector is not active.",
                status_code=409,
            )
        try:
            run = run_sync(connector, request=request, actor=request.user)
        except ConnectorNotConfigured as exc:
            raise _not_configured(str(exc)) from exc
        return Response(
            {"mode": connector.mode, "run": SyncRunSerializer(run).data},
            status=status.HTTP_200_OK,
        )


class ConnectorRunsView(ConnectorScopedView):
    allowed_query_params = {"page", "page_size", "status"}

    @extend_schema(responses=SyncRunSerializer(many=True))
    def get(self, request, connector_id):
        connector = self._connector(request, connector_id)
        queryset = SyncRun.objects.filter(clinic=connector.clinic, connector=connector).order_by(
            "-started_at", "-id"
        )
        value = (request.query_params.get("status") or "").strip()
        if value:
            queryset = queryset.filter(status=value)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SyncRunSerializer(page, many=True).data)


__all__ = [
    "ConnectorListCreateView",
    "ConnectorRunsView",
    "ConnectorSyncView",
    "FhirAppointmentView",
    "FhirEncounterView",
    "FhirPatientView",
]
