"""Scheduling API views (T110).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /appointment-types/``, ``GET|PATCH|DELETE /appointment-types/{id}/``
* ``GET|POST /operatories/``, ``GET|PATCH|DELETE /operatories/{id}/``
* ``GET|POST /availability-rules/``, ``PATCH|DELETE /availability-rules/{id}/``
* ``GET|POST /exceptions/``, ``PATCH|DELETE /exceptions/{id}/``
* ``GET /availability/?appointment_type=&provider=&location=&from=&to=``
* ``GET|POST /appointments/``, ``GET|PATCH /appointments/{id}/``
* ``POST /appointments/{id}/{confirm|check-in|start|complete|cancel|no-show|reschedule}/``
* ``GET /scheduling/metrics/?from=&to=&location=``

Every lookup runs through the tenant-scoped manager, so a foreign appointment
(another clinic's id) is an indistinguishable 404. Responses are
``Cache-Control: no-store``: appointments carry patient references and reasons.
"""

from __future__ import annotations

from django.db import transaction
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clinics.models import Location, Provider
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient

from .filters import (
    apply_appointment_filters,
    apply_appointment_ordering,
    apply_exception_filters,
    apply_exception_ordering,
    apply_rule_filters,
    apply_rule_ordering,
)
from .models import (
    Appointment,
    AppointmentType,
    AvailabilityRule,
    Operatory,
    ScheduleException,
    WaitlistEntry,
)
from .resources import availability_resource, parse_date_param
from .serializers import (
    AppointmentCancelSerializer,
    AppointmentPatchSerializer,
    AppointmentRescheduleSerializer,
    AppointmentSerializer,
    AppointmentTypeSerializer,
    AppointmentWriteSerializer,
    AvailabilityRuleSerializer,
    GapSerializer,
    OperatorySerializer,
    ProviderSerializer,
    RecommendationSerializer,
    ScheduleExceptionSerializer,
    WaitlistEntryPatchSerializer,
    WaitlistEntrySerializer,
    WaitlistEntryWriteSerializer,
    WaitlistOfferCreateSerializer,
    WaitlistOfferSerializer,
)
from .services.appointments import (
    cancel,
    create_appointment,
    reschedule,
    transition,
    update_appointment,
)
from .services.availability import compute_slots
from .services.metrics import metrics as compute_metrics
from .services.offers import claim_offer, create_offer, decline_offer
from .services.recommend import recommendations as compute_recommendations
from .services.waitlist import (
    create_entry as create_waitlist_entry,
)
from .services.waitlist import detect_gaps
from .services.waitlist import update_entry as update_waitlist_entry
from .status import ACTION_TARGETS

# --------------------------------------------------------------------------- #
# OpenAPI helpers
# --------------------------------------------------------------------------- #

DATE_RANGE_PARAMETERS = [
    OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=True),
    OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=True),
]

AVAILABILITY_SLOT_SCHEMA = inline_serializer(
    name="AvailabilitySlot",
    fields={
        "start": serializers.DateTimeField(),
        "end": serializers.DateTimeField(),
        "provider": serializers.UUIDField(allow_null=True),
        "provider_name": serializers.CharField(allow_null=True),
        "operatory": serializers.UUIDField(allow_null=True),
        "operatory_name": serializers.CharField(allow_null=True),
        "location": serializers.UUIDField(allow_null=True),
        "reason": serializers.CharField(),
    },
)

AVAILABILITY_SCHEMA = inline_serializer(
    name="AvailabilityResponse",
    fields={
        "window": serializers.DictField(),
        "slots": serializers.ListField(child=AVAILABILITY_SLOT_SCHEMA),
    },
)

METRICS_SCHEMA = inline_serializer(
    name="SchedulingMetrics",
    fields={
        "window": serializers.DictField(),
        "appointments": serializers.DictField(),
        "completed": serializers.IntegerField(),
        "cancelled": serializers.IntegerField(),
        "no_show": serializers.IntegerField(),
        "completion_rate": serializers.FloatField(),
        "cancellation_rate": serializers.FloatField(),
        "no_show_rate": serializers.FloatField(),
        "booked_minutes": serializers.IntegerField(),
        "available_minutes": serializers.IntegerField(),
        "utilisation": serializers.FloatField(),
        "patients": serializers.DictField(),
        "definition": serializers.DictField(),
    },
)


# --------------------------------------------------------------------------- #
# Base view
# --------------------------------------------------------------------------- #


class NoStoreMixin:
    """Scheduling responses carry PHI references and must never be cached."""

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _save_audited(serializer, *, action: str, request, clinic=None, instance=None):
    """Persist a config resource and its audit row in one transaction."""

    with transaction.atomic():
        if instance is None:
            saved = serializer.save(clinic=clinic)
        else:
            saved = serializer.save()
        record_audit(
            action,
            entity=saved,
            after=serializer.data,
            actor=request.user,
            request=request,
        )
    return saved


class SchedulingScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.view",)

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

    # -- lookups (tenant manager scopes to request.clinic) ------------------ #

    def _get_appointment(self, request, appointment_id) -> Appointment:
        appointment = (
            Appointment.objects.select_related(
                "appointment_type", "provider", "operatory", "location", "patient"
            )
            .filter(pk=appointment_id)
            .first()
        )
        if appointment is None:
            raise _not_found()
        return appointment


class TypesManageView(SchedulingScopedView):
    """Appointment types, operatories, rules and exceptions all need this perm."""

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.types.manage",)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


def _provider_snapshot(provider: Provider) -> dict:
    """Audit projection for a provider (ids/enums only, no member PII)."""

    return {
        "display_name": provider.display_name,
        "specialty": provider.specialty,
        "color": provider.color,
        "is_bookable": provider.is_bookable,
        "membership_id": str(provider.membership_id),
    }


class ProviderListCreateView(TypesManageView):
    allowed_query_params = {"page", "page_size", "ordering", "is_bookable", "search"}

    @extend_schema(responses=ProviderSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = (
            Provider.objects.filter(clinic=clinic)
            .select_related("membership", "membership__user")
            .order_by("display_name", "id")
        )
        active = (request.query_params.get("is_bookable") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_bookable=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_bookable=False)
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(display_name__icontains=search)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ProviderSerializer(page, many=True).data)

    @extend_schema(request=ProviderSerializer, responses={201: ProviderSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ProviderSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            provider = serializer.save(clinic=clinic)
            record_audit(
                "provider.created",
                entity=provider,
                after=_provider_snapshot(provider),
                actor=request.user,
                request=request,
            )
        return Response(ProviderSerializer(provider).data, status=status.HTTP_201_CREATED)


class ProviderDetailView(TypesManageView):
    def _get(self, request, provider_id) -> Provider:
        provider = (
            Provider.objects.select_related("membership", "membership__user")
            .filter(pk=provider_id)
            .first()
        )
        if provider is None:
            raise _not_found()
        return provider

    @extend_schema(responses=ProviderSerializer)
    def get(self, request, provider_id):
        self.clinic(request)
        return Response(ProviderSerializer(self._get(request, provider_id)).data)

    @extend_schema(request=ProviderSerializer, responses=ProviderSerializer)
    def patch(self, request, provider_id):
        clinic = self.clinic(request)
        provider = self._get(request, provider_id)
        serializer = ProviderSerializer(
            provider, data=request.data, partial=True, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            before = _provider_snapshot(provider)
            serializer.save()
            after = _provider_snapshot(provider)
            record_audit(
                "provider.updated",
                entity=provider,
                before=before,
                after=after,
                actor=request.user,
                request=request,
            )
        return Response(ProviderSerializer(provider).data)

    @extend_schema(responses={204: None})
    def delete(self, request, provider_id):
        from django.db.models.deletion import ProtectedError

        provider = self._get(request, provider_id)
        before = _provider_snapshot(provider)
        deleted_id = str(provider.pk)
        try:
            with transaction.atomic():
                provider.delete()
                record_audit(
                    "provider.deleted",
                    entity=provider,
                    entity_id=deleted_id,
                    before=before,
                    actor=request.user,
                    request=request,
                )
        except ProtectedError as exc:
            raise ApiError(
                "conflict",
                "This provider has appointments and cannot be deleted.",
                status_code=409,
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Appointment types
# --------------------------------------------------------------------------- #


class AppointmentTypeListCreateView(TypesManageView):
    allowed_query_params = {"page", "page_size", "ordering", "is_active", "search"}

    @extend_schema(responses=AppointmentTypeSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = AppointmentType.objects.filter(clinic=clinic).order_by("name", "id")
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AppointmentTypeSerializer(page, many=True).data)

    @extend_schema(request=AppointmentTypeSerializer, responses={201: AppointmentTypeSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = AppointmentTypeSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        appointment_type = _save_audited(
            serializer, action="appointment_type.created", request=request, clinic=clinic
        )
        return Response(
            AppointmentTypeSerializer(appointment_type).data,
            status=status.HTTP_201_CREATED,
        )


class AppointmentTypeDetailView(TypesManageView):
    def _get(self, request, type_id) -> AppointmentType:
        appointment_type = AppointmentType.objects.filter(pk=type_id).first()
        if appointment_type is None:
            raise _not_found()
        return appointment_type

    @extend_schema(responses=AppointmentTypeSerializer)
    def get(self, request, type_id):
        self.clinic(request)
        return Response(AppointmentTypeSerializer(self._get(request, type_id)).data)

    @extend_schema(request=AppointmentTypeSerializer, responses=AppointmentTypeSerializer)
    def patch(self, request, type_id):
        clinic = self.clinic(request)
        appointment_type = self._get(request, type_id)
        serializer = AppointmentTypeSerializer(
            appointment_type, data=request.data, partial=True, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        _save_audited(
            serializer,
            action="appointment_type.updated",
            request=request,
            instance=appointment_type,
        )
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request, type_id):
        from django.db.models.deletion import ProtectedError

        appointment_type = self._get(request, type_id)
        try:
            appointment_type.delete()
        except ProtectedError as exc:
            raise ApiError(
                "conflict",
                "This appointment type is in use and cannot be deleted.",
                status_code=409,
            ) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Operatories
# --------------------------------------------------------------------------- #


class OperatoryListCreateView(TypesManageView):
    allowed_query_params = {"page", "page_size", "ordering", "location", "is_active"}

    @extend_schema(responses=OperatorySerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Operatory.objects.filter(clinic=clinic).order_by("name", "id")
        location = (request.query_params.get("location") or "").strip()
        if location:
            queryset = queryset.filter(location_id=location)
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(OperatorySerializer(page, many=True).data)

    @extend_schema(request=OperatorySerializer, responses={201: OperatorySerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = OperatorySerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        operatory = _save_audited(
            serializer, action="operatory.created", request=request, clinic=clinic
        )
        return Response(OperatorySerializer(operatory).data, status=status.HTTP_201_CREATED)


class OperatoryDetailView(TypesManageView):
    def _get(self, request, operatory_id) -> Operatory:
        operatory = Operatory.objects.filter(pk=operatory_id).first()
        if operatory is None:
            raise _not_found()
        return operatory

    @extend_schema(responses=OperatorySerializer)
    def get(self, request, operatory_id):
        self.clinic(request)
        return Response(OperatorySerializer(self._get(request, operatory_id)).data)

    @extend_schema(request=OperatorySerializer, responses=OperatorySerializer)
    def patch(self, request, operatory_id):
        clinic = self.clinic(request)
        operatory = self._get(request, operatory_id)
        serializer = OperatorySerializer(
            operatory, data=request.data, partial=True, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request, operatory_id):
        operatory = self._get(request, operatory_id)
        operatory.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Availability rules
# --------------------------------------------------------------------------- #


class AvailabilityRuleListCreateView(TypesManageView):
    allowed_query_params = {
        "page",
        "page_size",
        "ordering",
        "provider",
        "location",
        "kind",
        "weekday",
    }

    @extend_schema(responses=AvailabilityRuleSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = apply_rule_ordering(
            apply_rule_filters(
                AvailabilityRule.objects.filter(clinic=clinic), request.query_params
            ),
            request.query_params,
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AvailabilityRuleSerializer(page, many=True).data)

    @extend_schema(
        request=AvailabilityRuleSerializer,
        responses={201: AvailabilityRuleSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = AvailabilityRuleSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        rule = _save_audited(
            serializer, action="availability_rule.created", request=request, clinic=clinic
        )
        return Response(AvailabilityRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


class AvailabilityRuleDetailView(TypesManageView):
    def _get(self, request, rule_id) -> AvailabilityRule:
        rule = AvailabilityRule.objects.filter(pk=rule_id).first()
        if rule is None:
            raise _not_found()
        return rule

    @extend_schema(request=AvailabilityRuleSerializer, responses=AvailabilityRuleSerializer)
    def patch(self, request, rule_id):
        clinic = self.clinic(request)
        rule = self._get(request, rule_id)
        serializer = AvailabilityRuleSerializer(
            rule, data=request.data, partial=True, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request, rule_id):
        self._get(request, rule_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Schedule exceptions
# --------------------------------------------------------------------------- #


class ScheduleExceptionListCreateView(TypesManageView):
    allowed_query_params = {"page", "page_size", "ordering", "provider", "location", "kind", "from", "to"}

    @extend_schema(responses=ScheduleExceptionSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = apply_exception_ordering(
            apply_exception_filters(
                ScheduleException.objects.filter(clinic=clinic), request.query_params
            ),
            request.query_params,
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ScheduleExceptionSerializer(page, many=True).data
        )

    @extend_schema(
        request=ScheduleExceptionSerializer,
        responses={201: ScheduleExceptionSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ScheduleExceptionSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        exception = _save_audited(
            serializer, action="schedule_exception.created", request=request, clinic=clinic
        )
        return Response(
            ScheduleExceptionSerializer(exception).data, status=status.HTTP_201_CREATED
        )


class ScheduleExceptionDetailView(TypesManageView):
    def _get(self, request, exception_id) -> ScheduleException:
        exception = ScheduleException.objects.filter(pk=exception_id).first()
        if exception is None:
            raise _not_found()
        return exception

    @extend_schema(
        request=ScheduleExceptionSerializer,
        responses=ScheduleExceptionSerializer,
    )
    def patch(self, request, exception_id):
        clinic = self.clinic(request)
        exception = self._get(request, exception_id)
        serializer = ScheduleExceptionSerializer(
            exception, data=request.data, partial=True, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request, exception_id):
        self._get(request, exception_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #

AVAILABILITY_PARAMETERS = [
    OpenApiParameter(
        name="appointment_type", type=str, location=OpenApiParameter.QUERY, required=True
    ),
    OpenApiParameter(name="provider", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="location", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=True),
    OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=True),
]


class AvailabilityView(SchedulingScopedView):
    allowed_query_params = {"appointment_type", "provider", "location", "from", "to"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.view",)

    @extend_schema(parameters=AVAILABILITY_PARAMETERS, responses=AVAILABILITY_SCHEMA)
    def get(self, request):
        clinic = self.clinic(request)
        raw_type = (request.query_params.get("appointment_type") or "").strip()
        if not raw_type:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"appointment_type": ["this parameter is required"]},
            )
        appointment_type = AppointmentType.objects.filter(pk=raw_type).first()
        if appointment_type is None:
            raise _not_found()

        provider = None
        raw_provider = (request.query_params.get("provider") or "").strip()
        if raw_provider:
            provider = Provider.objects.filter(pk=raw_provider).first()
            if provider is None:
                raise _not_found()

        location = None
        raw_location = (request.query_params.get("location") or "").strip()
        if raw_location:
            location = Location.objects.filter(pk=raw_location).first()
            if location is None:
                raise _not_found()

        from_date = parse_date_param(request.query_params.get("from"), "from")
        to_date = parse_date_param(request.query_params.get("to"), "to")

        result = compute_slots(
            clinic,
            appointment_type,
            from_date,
            to_date,
            provider=provider,
            location=location,
        )
        return Response(availability_resource(result))


# --------------------------------------------------------------------------- #
# Appointments
# --------------------------------------------------------------------------- #

APPOINTMENT_LIST_PARAMETERS = [
    OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="provider", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="location", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="patient", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="ordering", type=str, location=OpenApiParameter.QUERY, required=False),
]


class AppointmentListCreateView(SchedulingScopedView):
    allowed_query_params = {
        "page",
        "page_size",
        "ordering",
        "status",
        "provider",
        "location",
        "patient",
        "source",
        "from",
        "to",
        "start",
        "end",
        "start_at",
        "end_at",
    }

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("scheduling.view",)
        return ("scheduling.manage",)

    @extend_schema(
        parameters=APPOINTMENT_LIST_PARAMETERS,
        responses=AppointmentSerializer(many=True),
    )
    def get(self, request):
        clinic = self.clinic(request)
        queryset = apply_appointment_ordering(
            apply_appointment_filters(
                Appointment.objects.filter(clinic=clinic), request.query_params
            ),
            request.query_params,
        ).select_related("appointment_type", "provider", "operatory", "location", "patient")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AppointmentSerializer(page, many=True).data)

    @extend_schema(request=AppointmentWriteSerializer, responses={201: AppointmentSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = AppointmentWriteSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        key = (request.headers.get("Idempotency-Key") or data.get("idempotency_key") or "").strip()
        appointment = create_appointment(
            clinic,
            patient=Patient.objects.get(pk=data["patient"]),
            provider=Provider.objects.get(pk=data["provider"]),
            appointment_type=AppointmentType.objects.get(pk=data["appointment_type"]),
            location=Location.objects.get(pk=data["location"]),
            operatory=(
                Operatory.objects.filter(pk=data["operatory"]).first()
                if data.get("operatory")
                else None
            ),
            start_at=data["start_at"],
            end_at=data.get("end_at"),
            source=data.get("source"),
            reason=data.get("reason", ""),
            notes=data.get("notes", ""),
            idempotency_key=key,
            status=data.get("status"),
            actor=request.user,
            request=request,
        )
        return Response(
            AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED
        )


class AppointmentDetailView(SchedulingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("scheduling.view",)
        return ("scheduling.manage",)

    @extend_schema(responses=AppointmentSerializer)
    def get(self, request, appointment_id):
        self.clinic(request)
        appointment = self._get_appointment(request, appointment_id)
        return Response(AppointmentSerializer(appointment).data)

    @extend_schema(request=AppointmentPatchSerializer, responses=AppointmentSerializer)
    def patch(self, request, appointment_id):
        clinic = self.clinic(request)
        appointment = self._get_appointment(request, appointment_id)
        serializer = AppointmentPatchSerializer(
            data=request.data, context={"clinic": clinic}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        updated = update_appointment(
            appointment,
            fields=serializer.to_model_fields(),
            version=serializer.validated_data.get("version"),
            actor=request.user,
            request=request,
        )
        return Response(AppointmentSerializer(updated).data)


ACTION_PARAMETERS = [
    OpenApiParameter(
        name="action",
        type=str,
        location=OpenApiParameter.PATH,
        enum=sorted(ACTION_TARGETS),
    )
]


class AppointmentActionView(SchedulingScopedView):
    """``POST /appointments/{id}/{confirm|check-in|start|complete|cancel|no-show}/``."""

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.manage",)

    @extend_schema(
        request=AppointmentCancelSerializer,
        responses=AppointmentSerializer,
        operation_id="appointments_action_create",
    )
    def post(self, request, appointment_id, action):
        target = ACTION_TARGETS.get(action)
        if target is None:
            raise _not_found()
        appointment = self._get_appointment(request, appointment_id)

        if action == "cancel":
            serializer = AppointmentCancelSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            appointment = cancel(
                appointment,
                reason=serializer.validated_data.get("reason", ""),
                actor=request.user,
                request=request,
            )
        else:
            appointment = transition(
                appointment, target, actor=request.user, request=request
            )
        return Response(AppointmentSerializer(appointment).data)


class AppointmentRescheduleView(SchedulingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.manage",)

    @extend_schema(
        request=AppointmentRescheduleSerializer,
        responses={201: AppointmentSerializer},
    )
    def post(self, request, appointment_id):
        clinic = self.clinic(request)
        appointment = self._get_appointment(request, appointment_id)
        serializer = AppointmentRescheduleSerializer(
            data=request.data, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        operatory_id = serializer.validated_data.get("operatory")
        operatory = (
            Operatory.objects.filter(pk=operatory_id).first() if operatory_id else None
        )
        new = reschedule(
            appointment,
            serializer.validated_data["start_at"],
            operatory=operatory,
            reason=serializer.validated_data.get("reason", ""),
            actor=request.user,
            request=request,
        )
        return Response(AppointmentSerializer(new).data, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


class SchedulingMetricsView(SchedulingScopedView):
    allowed_query_params = {"from", "to", "location"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.view",)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(
                name="location", type=str, location=OpenApiParameter.QUERY, required=False
            ),
        ],
        responses=METRICS_SCHEMA,
    )
    def get(self, request):
        clinic = self.clinic(request)
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
                raise _not_found()

        return Response(compute_metrics(clinic, from_date, to_date, location=location))


# --------------------------------------------------------------------------- #
# Waitlist
# --------------------------------------------------------------------------- #


class WaitlistManageView(SchedulingScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("waitlist.manage",)


class WaitlistListCreateView(WaitlistManageView):
    allowed_query_params = {"page", "page_size", "status", "patient", "urgency", "ordering"}

    @extend_schema(responses=WaitlistEntrySerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = WaitlistEntry.objects.filter(clinic=clinic)
        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        patient = (request.query_params.get("patient") or "").strip()
        if patient:
            queryset = queryset.filter(patient_id=patient)
        ordering = (request.query_params.get("ordering") or "").strip()
        if ordering in {"urgency", "-urgency", "window_start", "-window_start", "-created_at"}:
            queryset = queryset.order_by(ordering, "id")
        else:
            queryset = queryset.order_by("-urgency", "window_start", "id")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            WaitlistEntrySerializer(page, many=True).data
        )

    @extend_schema(
        request=WaitlistEntryWriteSerializer, responses={201: WaitlistEntrySerializer}
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = WaitlistEntryWriteSerializer(
            data=request.data, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entry = create_waitlist_entry(
            clinic,
            patient=Patient.objects.get(pk=data["patient"]),
            appointment_type=AppointmentType.objects.get(pk=data["appointment_type"]),
            provider_pref=(
                Provider.objects.filter(pk=data["provider_pref"]).first()
                if data.get("provider_pref")
                else None
            ),
            window_start=data["window_start"],
            window_end=data["window_end"],
            urgency=data.get("urgency", 3),
            consent_snapshot=data.get("consent_snapshot") or {},
            notes=data.get("notes", ""),
            actor=request.user,
            request=request,
        )
        return Response(
            WaitlistEntrySerializer(entry).data, status=status.HTTP_201_CREATED
        )


class WaitlistDetailView(WaitlistManageView):
    def _get(self, request, entry_id) -> WaitlistEntry:
        entry = WaitlistEntry.objects.filter(
            clinic=self.clinic(request), pk=entry_id
        ).first()
        if entry is None:
            raise _not_found()
        return entry

    @extend_schema(responses=WaitlistEntrySerializer)
    def get(self, request, entry_id):
        return Response(WaitlistEntrySerializer(self._get(request, entry_id)).data)

    @extend_schema(request=WaitlistEntryPatchSerializer, responses=WaitlistEntrySerializer)
    def patch(self, request, entry_id):
        entry = self._get(request, entry_id)
        serializer = WaitlistEntryPatchSerializer(
            data=request.data, partial=True, context={"clinic": request.clinic}
        )
        serializer.is_valid(raise_exception=True)
        entry = update_waitlist_entry(
            entry,
            fields=serializer.to_model_fields(),
            actor=request.user,
            request=request,
        )
        return Response(WaitlistEntrySerializer(entry).data)


class WaitlistOfferCreateView(WaitlistManageView):
    @extend_schema(
        request=WaitlistOfferCreateSerializer, responses={201: WaitlistOfferSerializer}
    )
    def post(self, request, entry_id):
        clinic = self.clinic(request)
        entry = WaitlistEntry.objects.filter(clinic=clinic, pk=entry_id).first()
        if entry is None:
            raise _not_found()
        serializer = WaitlistOfferCreateSerializer(
            data=request.data, context={"clinic": clinic}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        provider = (
            Provider.objects.filter(pk=data["provider"]).first()
            if data.get("provider")
            else None
        )
        operatory = (
            Operatory.objects.filter(pk=data["operatory"]).first()
            if data.get("operatory")
            else None
        )
        offer, raw_token = create_offer(
            entry,
            start_at=data["start_at"],
            provider=provider,
            operatory=operatory,
            ttl_minutes=data.get("ttl_minutes"),
            actor=request.user,
            request=request,
        )
        return Response(
            WaitlistOfferSerializer(offer, context={"token": raw_token}).data,
            status=status.HTTP_201_CREATED,
        )


class OfferClaimView(WaitlistManageView):
    @extend_schema(request=None, responses=WaitlistOfferSerializer)
    def post(self, request, token):
        self.clinic(request)
        offer = claim_offer(token, actor=request.user, request=request)
        return Response(WaitlistOfferSerializer(offer).data)


class OfferDeclineView(WaitlistManageView):
    @extend_schema(request=None, responses=WaitlistOfferSerializer)
    def post(self, request, token):
        self.clinic(request)
        offer = decline_offer(token, actor=request.user, request=request)
        return Response(WaitlistOfferSerializer(offer).data)


class RecommendationsView(SchedulingScopedView):
    allowed_query_params = {"date", "type"}

    # Returns a bare array; opt out of the paginated envelope.
    pagination_class = None

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.view",)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="date", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="type", type=str, location=OpenApiParameter.QUERY, required=True),
        ],
        responses=RecommendationSerializer(many=True),
    )
    def get(self, request):
        clinic = self.clinic(request)
        on_date = parse_date_param(request.query_params.get("date"), "date")
        raw_type = (request.query_params.get("type") or "").strip()
        if not raw_type:
            raise ApiError(
                "validation_error",
                "The request could not be processed.",
                status_code=400,
                field_errors={"type": ["this parameter is required"]},
            )
        appointment_type = AppointmentType.objects.filter(pk=raw_type).first()
        if appointment_type is None:
            raise _not_found()
        return Response(
            compute_recommendations(clinic, on_date, appointment_type)
        )


class GapsView(SchedulingScopedView):
    allowed_query_params = {"from", "to", "type", "provider", "location"}

    # Returns a bare array; opt out of the paginated envelope.
    pagination_class = None

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("scheduling.view",)

    @extend_schema(
        parameters=[
            OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=True),
            OpenApiParameter(name="type", type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name="provider", type=str, location=OpenApiParameter.QUERY),
            OpenApiParameter(name="location", type=str, location=OpenApiParameter.QUERY),
        ],
        responses=GapSerializer(many=True),
    )
    def get(self, request):
        clinic = self.clinic(request)
        from_date = parse_date_param(request.query_params.get("from"), "from")
        to_date = parse_date_param(request.query_params.get("to"), "to")

        appointment_type = None
        raw_type = (request.query_params.get("type") or "").strip()
        if raw_type:
            appointment_type = AppointmentType.objects.filter(pk=raw_type).first()
            if appointment_type is None:
                raise _not_found()

        provider = None
        raw_provider = (request.query_params.get("provider") or "").strip()
        if raw_provider:
            provider = Provider.objects.filter(pk=raw_provider).first()
            if provider is None:
                raise _not_found()

        location = None
        raw_location = (request.query_params.get("location") or "").strip()
        if raw_location:
            location = Location.objects.filter(pk=raw_location).first()
            if location is None:
                raise _not_found()

        gaps = detect_gaps(
            clinic,
            from_date,
            to_date,
            appointment_type=appointment_type,
            provider=provider,
            location=location,
        )
        return Response(GapSerializer(gaps, many=True).data)


__all__ = [
    "AppointmentActionView",
    "AppointmentDetailView",
    "AppointmentListCreateView",
    "AppointmentRescheduleView",
    "AppointmentTypeDetailView",
    "AppointmentTypeListCreateView",
    "AvailabilityRuleDetailView",
    "AvailabilityRuleListCreateView",
    "AvailabilityView",
    "GapsView",
    "OfferClaimView",
    "OfferDeclineView",
    "OperatoryDetailView",
    "OperatoryListCreateView",
    "ProviderDetailView",
    "ProviderListCreateView",
    "RecommendationsView",
    "ScheduleExceptionDetailView",
    "ScheduleExceptionListCreateView",
    "SchedulingMetricsView",
    "WaitlistDetailView",
    "WaitlistListCreateView",
    "WaitlistOfferCreateView",
]
