"""Public booking API views (T112).

URL layout (mounted under ``/api/v1/``):

* ``GET  /public/clinics/{slug}/``
* ``GET  /public/clinics/{slug}/availability/?type=&location=&from=&to=``
* ``POST /public/bookings/``
* ``POST /public/bookings/{reference}/verify/``
* ``GET|POST /public/links/{token}/``

Every view is unauthenticated: ``authentication_classes`` is empty and the
permission is ``AllowAny``. Access control is by clinic slug, a signed link
token or the booking reference, never by session. Responses are
``Cache-Control: no-store`` because booking payloads carry scheduling data.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.clinics.models import Clinic, Location, Provider
from apps.core.errors import ApiError
from apps.core.tenancy import with_clinic
from apps.scheduling.models import AppointmentType
from apps.scheduling.resources import availability_resource, parse_date_param

from .resources import public_clinic_resource
from .serializers import (
    PublicBookingCreateSerializer,
    PublicLinkActionSerializer,
    PublicVerifySerializer,
)
from .services.booking import (
    cancel_via_link,
    create_booking,
    link_summary,
    reschedule_via_link,
    verify_booking,
)
from .services.policy import get_policy
from .services.slots import public_slots
from .throttles import PublicBookingThrottle


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class PublicBaseView(NoStoreMixin, APIView):
    """Unauthenticated base view; every public endpoint extends this."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get_clinic(self, slug: str) -> Clinic:
        clinic = Clinic.objects.filter(slug=slug, status=Clinic.Status.ACTIVE).first()
        if clinic is None:
            raise _not_found()
        return clinic

    @contextmanager
    def clinic_scope(self, clinic: Clinic):
        """The clinic context of a public endpoint.

        The public surface is scoped to one clinic by its public slug, so the
        clinic context is opened for the request: the locations, the
        appointment types and the providers it publishes are addressed by the
        clinic (ADR-013).
        """

        with with_clinic(clinic):
            yield clinic


# --- OpenAPI response shapes ------------------------------------------------ #

PUBLIC_CLINIC_SCHEMA = inline_serializer(
    name="PublicClinic",
    fields={
        "id": serializers.UUIDField(),
        "name": serializers.CharField(),
        "slug": serializers.CharField(),
        "timezone": serializers.CharField(),
        "currency": serializers.CharField(),
        "locale": serializers.CharField(),
        "country": serializers.CharField(),
        "phone": serializers.CharField(),
        "address_json": serializers.DictField(),
        "locations": serializers.ListField(child=serializers.DictField()),
        "appointment_types": serializers.ListField(child=serializers.DictField()),
        "providers": serializers.ListField(child=serializers.DictField()),
        "policy": serializers.DictField(),
    },
)

PUBLIC_BOOKING_CREATED_SCHEMA = inline_serializer(
    name="PublicBookingCreated",
    fields={
        "reference": serializers.CharField(),
        "appointment_id": serializers.UUIDField(),
        "starts_at": serializers.DateTimeField(),
    },
)

PUBLIC_LINK_SUMMARY_SCHEMA = inline_serializer(
    name="PublicLinkSummary",
    fields={
        "reference": serializers.CharField(allow_null=True),
        "appointment_id": serializers.UUIDField(),
        "status": serializers.CharField(),
        "starts_at": serializers.DateTimeField(),
        "ends_at": serializers.DateTimeField(),
        "appointment_type": serializers.CharField(),
        "provider_name": serializers.CharField(),
        "location_name": serializers.CharField(),
        "patient": serializers.DictField(),
        "allowed_actions": serializers.ListField(child=serializers.CharField()),
        "cancel_cutoff_hours": serializers.IntegerField(),
        "reschedule_cutoff_hours": serializers.IntegerField(),
    },
)


def _resolve_type(clinic: Clinic, raw: str | None) -> AppointmentType:
    value = (raw or "").strip()
    if not value:
        raise _validation("type", "this parameter is required")
    queryset = AppointmentType.all_objects.filter(
        clinic=clinic, is_active=True, public_bookable=True
    )
    try:
        uuid.UUID(value)
    except (TypeError, ValueError):
        appointment_type = queryset.filter(code=value.lower()).first()
    else:
        appointment_type = queryset.filter(pk=value).first()
    if appointment_type is None:
        raise _not_found()
    return appointment_type


class PublicClinicView(PublicBaseView):
    @extend_schema(responses=PUBLIC_CLINIC_SCHEMA)
    def get(self, request, slug):
        clinic = self.get_clinic(slug)
        with self.clinic_scope(clinic):
            policy = get_policy(clinic)
            locations = list(
                Location.all_objects.filter(
                    clinic=clinic, is_active=True
                ).order_by("name", "id")
            )
            appointment_types = list(
                AppointmentType.all_objects.filter(
                    clinic=clinic, is_active=True, public_bookable=True
                ).order_by("name", "id")
            )
            providers = list(
                Provider.all_objects.filter(clinic=clinic, is_bookable=True).order_by(
                    "display_name", "id"
                )
            )
            return Response(
                public_clinic_resource(
                    clinic,
                    policy,
                    locations=locations,
                    appointment_types=appointment_types,
                    providers=providers,
                )
            )


AVAILABILITY_PARAMETERS = [
    OpenApiParameter(name="type", type=str, location=OpenApiParameter.QUERY, required=True),
    OpenApiParameter(name="location", type=str, location=OpenApiParameter.QUERY, required=False),
    OpenApiParameter(name="from", type=str, location=OpenApiParameter.QUERY, required=True),
    OpenApiParameter(name="to", type=str, location=OpenApiParameter.QUERY, required=True),
]


class PublicAvailabilityView(PublicBaseView):
    allowed_query_params = {"type", "location", "from", "to"}

    @extend_schema(parameters=AVAILABILITY_PARAMETERS, responses=dict)
    def get(self, request, slug):
        clinic = self.get_clinic(slug)
        with self.clinic_scope(clinic):
            appointment_type = _resolve_type(clinic, request.query_params.get("type"))

            location = None
            raw_location = (request.query_params.get("location") or "").strip()
            if raw_location:
                location = (
                    Location.all_objects.filter(pk=raw_location, clinic=clinic).first()
                )
                if location is None:
                    raise _not_found()

            from_date = parse_date_param(request.query_params.get("from"), "from")
            to_date = parse_date_param(request.query_params.get("to"), "to")

            result = public_slots(
                clinic,
                appointment_type,
                from_date,
                to_date,
                location=location,
            )
            return Response(availability_resource(result))


class PublicBookingCreateView(PublicBaseView):
    throttle_classes = [PublicBookingThrottle]

    @extend_schema(
        request=PublicBookingCreateSerializer,
        responses={201: PUBLIC_BOOKING_CREATED_SCHEMA},
    )
    def post(self, request):
        # Honeypot: a filled hidden field is silently discarded (202) without
        # creating anything, and without validating the rest of the payload.
        data = request.data if hasattr(request.data, "get") else {}
        if str(data.get("website") or "").strip():
            return Response({"status": "accepted"}, status=status.HTTP_202_ACCEPTED)

        serializer = PublicBookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        appointment_type = AppointmentType.all_objects.filter(
            pk=payload["appointment_type"], is_active=True, public_bookable=True
        ).first()
        if appointment_type is None:
            raise _not_found()

        result = create_booking(
            appointment_type=appointment_type,
            slot_start=payload["slot_start"],
            patient_data=payload["patient"],
            consents=payload.get("consents") or {},
            location_id=payload.get("location"),
            idempotency_key=payload.get("idempotency_key", ""),
            request=request,
        )
        return Response(result, status=status.HTTP_201_CREATED)


class PublicBookingVerifyView(PublicBaseView):
    @extend_schema(request=PublicVerifySerializer, responses=dict)
    def post(self, request, reference):
        serializer = PublicVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verified = verify_booking(
            reference,
            serializer.validated_data.get("code", ""),
            request=request,
        )
        return Response(
            {
                "reference": verified.reference,
                "email_verified": verified.email_verified_at is not None,
                "email_verified_at": verified.email_verified_at,
            }
        )


class PublicLinkView(PublicBaseView):
    @extend_schema(responses=PUBLIC_LINK_SUMMARY_SCHEMA)
    def get(self, request, token):
        return Response(link_summary(token))

    @extend_schema(request=PublicLinkActionSerializer, responses=dict)
    def post(self, request, token):
        serializer = PublicLinkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        if action == "cancel":
            return Response(cancel_via_link(token, request=request))
        start_at = serializer.validated_data.get("start_at")
        if start_at is None:
            raise _validation("start_at", "this field is required to reschedule")
        return Response(reschedule_via_link(token, start_at, request=request))


__all__ = [
    "PublicAvailabilityView",
    "PublicBookingCreateView",
    "PublicBookingVerifyView",
    "PublicClinicView",
    "PublicLinkView",
]
