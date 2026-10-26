"""Clinic, location, member and invitation HTTP views."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services.auth import active_memberships
from apps.clinics.models import Invitation, Location, Membership
from apps.clinics.serializers import (
    ClinicCreateSerializer,
    ClinicSerializer,
    ClinicUpdateSerializer,
    ClinicWithRoleSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    LocationCreateSerializer,
    LocationSerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
)
from apps.clinics.serializers_audit import AuditEventSerializer
from apps.clinics.services.invitations import (
    accept_invitation,
    create_invitation,
    revoke_invitation,
)
from apps.clinics.services.members import (
    create_clinic_with_owner,
    remove_membership,
    update_membership,
)
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.idempotency import IDEMPOTENCY_KEY_PARAMETER
from apps.core.models import AuditEvent
from apps.core.pagination import AuditCursorPagination
from apps.core.permissions import HasClinicPermission
from apps.core.redaction import changed_field_names
from apps.core.schema import CLINIC_ID_PARAMETER

ClinicCreatedResponse = inline_serializer(
    name="ClinicCreatedResponse",
    fields={
        "clinic": ClinicSerializer(),
        "membership": MembershipSerializer(),
    },
)

InvitationAcceptedResponse = inline_serializer(
    name="InvitationAcceptedResponse",
    fields={
        "membership": MembershipSerializer(),
        "clinic": ClinicSerializer(),
    },
)


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


_LOCATION_AUDIT_FIELDS = ("name", "timezone", "address_json", "phone", "is_active")


def _location_snapshot(location: Location, fields=_LOCATION_AUDIT_FIELDS) -> dict:
    return {field: getattr(location, field) for field in fields}


class ScopedClinicView(APIView):
    """Base view for endpoints addressed by ``/clinics/{id}/...``."""

    permission_classes = [HasClinicPermission]

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("clinic.view",)

    def get_clinic(self, request, clinic_id: uuid.UUID):
        scope_error = getattr(request, "clinic_scope_error", None)
        if scope_error is not None:
            raise scope_error

        clinic = getattr(request, "clinic", None)
        if clinic is None:
            if getattr(request, "clinic_required", False):
                raise ApiError(
                    "clinic_required",
                    "Select a clinic with the X-Clinic-Id header.",
                    status_code=400,
                )
            raise _not_found()

        if clinic.id != clinic_id:
            # A valid header for a different clinic must never reveal whether
            # the requested object exists.
            raise _not_found()
        return clinic


class ClinicListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ClinicWithRoleSerializer(many=True)})
    def get(self, request):
        memberships = list(active_memberships(request.user))
        payload = []
        for membership in memberships:
            clinic_data = ClinicSerializer(membership.clinic).data
            payload.append(
                {
                    **clinic_data,
                    "role": membership.role,
                    "membership_id": str(membership.id),
                }
            )
        return Response(payload)

    @extend_schema(
        request=ClinicCreateSerializer,
        responses={201: ClinicCreatedResponse},
        parameters=[IDEMPOTENCY_KEY_PARAMETER],
    )
    def post(self, request):
        serializer = ClinicCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        clinic, membership = create_clinic_with_owner(
            request.user,
            name=data["name"],
            slug=data["slug"],
            timezone_name=data["timezone"],
            currency=data["currency"],
            locale=data["locale"],
            country=data["country"],
        )
        return Response(
            {
                "clinic": ClinicSerializer(clinic).data,
                "membership": MembershipSerializer(membership).data,
            },
            status=status.HTTP_201_CREATED,
        )


class ClinicDetailView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("clinic.view",)
        return ("clinic.manage",)

    @extend_schema(parameters=[CLINIC_ID_PARAMETER], responses=ClinicSerializer)
    def get(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        return Response(ClinicSerializer(clinic).data)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        request=ClinicUpdateSerializer,
        responses=ClinicSerializer,
    )
    def patch(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        serializer = ClinicUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = list(serializer.validated_data)
        # Mutation + audit share one transaction: a failed audit rolls the
        # settings change back (T103 constraint).
        with transaction.atomic():
            before = {field: getattr(clinic, field) for field in fields}
            for field, value in serializer.validated_data.items():
                setattr(clinic, field, value)
            clinic.save()
            after = {field: getattr(clinic, field) for field in fields}
            changed = changed_field_names(before, after)
            record_audit(
                "clinic.updated",
                entity=clinic,
                before={field: before[field] for field in changed},
                after={field: after[field] for field in changed},
                actor=request.user,
                request=request,
            )
        return Response(ClinicSerializer(clinic).data)


class LocationListCreateView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("clinic.view",)
        return ("clinic.manage",)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        responses={200: LocationSerializer(many=True)},
    )
    def get(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        queryset = Location.all_objects.filter(clinic=clinic).order_by("name", "id")
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return Response(LocationSerializer(queryset, many=True).data)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER, IDEMPOTENCY_KEY_PARAMETER],
        request=LocationCreateSerializer,
        responses={201: LocationSerializer},
    )
    def post(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        serializer = LocationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            location = Location(clinic=clinic, **serializer.validated_data)
            location.save()
            record_audit(
                "location.created",
                entity=location,
                after=_location_snapshot(location),
                actor=request.user,
                request=request,
            )
        return Response(
            LocationSerializer(location).data,
            status=status.HTTP_201_CREATED,
        )


class LocationDetailView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("clinic.view",)
        return ("clinic.manage",)

    def _get_location(self, request, clinic_id, location_id) -> Location:
        clinic = self.get_clinic(request, clinic_id)
        location = Location.all_objects.filter(pk=location_id, clinic=clinic).first()
        if location is None:
            raise _not_found()
        return location

    @extend_schema(parameters=[CLINIC_ID_PARAMETER], responses=LocationSerializer)
    def get(self, request, clinic_id, location_id):
        location = self._get_location(request, clinic_id, location_id)
        return Response(LocationSerializer(location).data)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        request=LocationSerializer,
        responses=LocationSerializer,
    )
    def patch(self, request, clinic_id, location_id):
        location = self._get_location(request, clinic_id, location_id)
        serializer = LocationSerializer(location, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            before = _location_snapshot(location)
            serializer.save()
            after = _location_snapshot(location)
            changed = changed_field_names(before, after)
            record_audit(
                "location.updated",
                entity=location,
                before={field: before[field] for field in changed},
                after={field: after[field] for field in changed},
                actor=request.user,
                request=request,
            )
        return Response(serializer.data)

    @extend_schema(parameters=[CLINIC_ID_PARAMETER], responses={204: None})
    def delete(self, request, clinic_id, location_id):
        location = self._get_location(request, clinic_id, location_id)
        with transaction.atomic():
            before = _location_snapshot(location)
            # ``Model.delete()`` clears the pk, so capture the id for the audit row.
            deleted_id = str(location.pk)
            location.delete()
            record_audit(
                "location.deleted",
                entity=location,
                entity_id=deleted_id,
                before=before,
                actor=request.user,
                request=request,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberListView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("members.manage",)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        responses={200: MembershipSerializer(many=True)},
    )
    def get(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        memberships = (
            Membership.all_objects.filter(clinic=clinic)
            .select_related("user")
            .order_by("created_at", "id")
        )
        return Response(MembershipSerializer(memberships, many=True).data)


class MemberDetailView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("members.manage",)

    def _get_membership(self, request, clinic_id, membership_id) -> Membership:
        clinic = self.get_clinic(request, clinic_id)
        membership = (
            Membership.all_objects.filter(pk=membership_id, clinic=clinic)
            .select_related("user")
            .first()
        )
        if membership is None:
            raise _not_found()
        return membership

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        request=MembershipUpdateSerializer,
        responses=MembershipSerializer,
    )
    def patch(self, request, clinic_id, membership_id):
        membership = self._get_membership(request, clinic_id, membership_id)
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = update_membership(
            membership,
            role=serializer.validated_data.get("role"),
            status=serializer.validated_data.get("status"),
            permission_overrides=serializer.validated_data.get("permission_overrides"),
            actor=request.user,
            request=request,
        )
        return Response(MembershipSerializer(updated).data)

    @extend_schema(parameters=[CLINIC_ID_PARAMETER], responses={204: None})
    def delete(self, request, clinic_id, membership_id):
        membership = self._get_membership(request, clinic_id, membership_id)
        remove_membership(membership, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("members.manage",)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        responses={200: InvitationSerializer(many=True)},
    )
    def get(self, request, clinic_id):
        """List the clinic's pending invitations (accepted/revoked excluded)."""

        clinic = self.get_clinic(request, clinic_id)
        invitations = (
            Invitation.objects.filter(
                clinic=clinic,
                accepted_at__isnull=True,
                revoked_at__isnull=True,
            )
            .select_related("clinic")
            .order_by("created_at", "id")
        )
        return Response(InvitationSerializer(invitations, many=True).data)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER, IDEMPOTENCY_KEY_PARAMETER],
        request=InvitationCreateSerializer,
        responses={201: InvitationSerializer},
    )
    def post(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation, raw_token = create_invitation(
            clinic,
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
            actor=request.user,
            request=request,
        )
        payload = dict(InvitationSerializer(invitation).data)
        if settings.DEBUG:
            # Development convenience only; never returned in production.
            payload["token"] = raw_token
        return Response(payload, status=status.HTTP_201_CREATED)


class InvitationRevokeView(ScopedClinicView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("members.manage",)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER],
        request=None,
        responses={204: None},
    )
    def post(self, request, clinic_id, invitation_id):
        clinic = self.get_clinic(request, clinic_id)
        invitation = Invitation.objects.filter(pk=invitation_id, clinic=clinic).first()
        if invitation is None:
            raise _not_found()
        revoke_invitation(invitation, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=InvitationAcceptedResponse)
    def post(self, request, token):
        membership = accept_invitation(request.user, token, request=request)
        return Response(
            {
                "membership": MembershipSerializer(membership).data,
                "clinic": ClinicSerializer(membership.clinic).data,
            }
        )


AUDIT_FILTER_PARAMETERS = [
    OpenApiParameter(
        name="entity_type",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Exact entity type (for example ``clinics.membership``).",
    ),
    OpenApiParameter(
        name="entity_id",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Exact entity id (UUID or integer, as a string).",
    ),
    OpenApiParameter(
        name="from",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description=(
            "Inclusive lower bound, ISO-8601 date or datetime. Date ranges "
            "require both ``from`` and ``to``."
        ),
    ),
    OpenApiParameter(
        name="to",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive upper bound; requires ``from``.",
    ),
]

MAX_ENTITY_TYPE_LENGTH = 128
MAX_ENTITY_ID_LENGTH = 64


def _validation(field: str, message: str) -> ApiError:
    return ApiError(
        "validation_error",
        "The request could not be processed.",
        status_code=400,
        field_errors={field: [message]},
    )


def _parse_audit_bound(raw: str | None, field: str, *, end_of_day: bool) -> datetime | None:
    """Parse ``from``/``to`` into an aware datetime, or raise a 400."""

    value = (raw or "").strip()
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is not None:
            parsed = datetime.combine(
                day,
                time.max if end_of_day else time.min,
            )
    if parsed is None:
        raise _validation(
            field,
            "enter a valid ISO-8601 date or datetime",
        )
    if django_timezone.is_naive(parsed):
        parsed = django_timezone.make_aware(parsed, django_timezone.get_current_timezone())
    return parsed


class ClinicAuditView(ScopedClinicView):
    """Read-only, owner-only audit stream for one clinic."""

    pagination_class = AuditCursorPagination

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("audit.view",)

    @extend_schema(
        parameters=[CLINIC_ID_PARAMETER, *AUDIT_FILTER_PARAMETERS],
        responses=AuditEventSerializer(many=True),
    )
    def get(self, request, clinic_id):
        clinic = self.get_clinic(request, clinic_id)
        queryset = AuditEvent.objects.filter(clinic=clinic).order_by("-id")

        entity_type = (request.query_params.get("entity_type") or "").strip()
        if entity_type:
            if len(entity_type) > MAX_ENTITY_TYPE_LENGTH:
                raise _validation("entity_type", "is too long")
            queryset = queryset.filter(entity_type=entity_type)

        entity_id = (request.query_params.get("entity_id") or "").strip()
        if entity_id:
            if len(entity_id) > MAX_ENTITY_ID_LENGTH:
                raise _validation("entity_id", "is too long")
            queryset = queryset.filter(entity_id=entity_id)

        raw_from = request.query_params.get("from")
        raw_to = request.query_params.get("to")
        if bool((raw_from or "").strip()) != bool((raw_to or "").strip()):
            raise _validation("from", "date ranges require both `from` and `to`")

        start = _parse_audit_bound(raw_from, "from", end_of_day=False)
        end = _parse_audit_bound(raw_to, "to", end_of_day=True)
        if start is not None and end is not None and start > end:
            raise _validation("from", "must not be after `to`")
        if start is not None:
            queryset = queryset.filter(created_at__gte=start)
        if end is not None:
            queryset = queryset.filter(created_at__lte=end)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AuditEventSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
