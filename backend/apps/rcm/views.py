"""RCM API views (T134).

URL layout (mounted under ``/api/v1/``):

* ``GET|POST /payers/``
* ``GET|POST /insurance-policies/``
* ``POST /eligibility-checks/``
* ``GET|POST /claims/``
* ``POST /claims/{id}/submit/``
* ``GET /claims/{id}/events/``
* ``POST /remittances/``
* ``GET /rcm/reconciliation/``

The module is feature-flagged off by default (``RCM_ENABLED=false``): every
endpoint answers 404 while disabled, before authentication or permission checks,
so a self-pay deployment never exposes the surface. Responses are
``Cache-Control: no-store``.
"""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice
from apps.core.audit import record as record_audit
from apps.core.errors import ApiError
from apps.core.pagination import StandardPagination, StandardPaginationViewMixin
from apps.core.permissions import HasClinicPermission
from apps.patients.models import Patient

from .models import Claim, InsurancePolicy, Payer
from .serializers import (
    ClaimCreateSerializer,
    ClaimEventSerializer,
    ClaimSerializer,
    EligibilityCheckCreateSerializer,
    EligibilityCheckSerializer,
    InsurancePolicySerializer,
    PayerSerializer,
    ReconciliationSerializer,
    RemittanceCreateSerializer,
    RemittanceSerializer,
)
from .services import claims as claim_service
from .services import eligibility as eligibility_service
from .services import reconcile as reconcile_service
from .services import remittance as remittance_service


def rcm_enabled() -> bool:
    """True when the RCM module is enabled for this deployment."""

    return bool(getattr(settings, "RCM_ENABLED", False))


def _not_found() -> ApiError:
    return ApiError("not_found", "Not found.", status_code=404)


class NoStoreMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class RcmScopedView(StandardPaginationViewMixin, NoStoreMixin, APIView):
    permission_classes = [HasClinicPermission]

    def initial(self, request, *args, **kwargs):
        # Clean feature flag: disabled means 404 on every RCM endpoint.
        if not rcm_enabled():
            raise _not_found()
        super().initial(request, *args, **kwargs)

    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("rcm.view",)

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

    def claim(self, request, claim_id) -> Claim:
        claim = (
            Claim.objects.filter(clinic=self.clinic(request), pk=claim_id)
            .select_related("policy", "payer", "invoice")
            .first()
        )
        if claim is None:
            raise _not_found()
        return claim


# --------------------------------------------------------------------------- #
# Payers and policies
# --------------------------------------------------------------------------- #


class PayerListCreateView(RcmScopedView):
    allowed_query_params = {"page", "page_size", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("rcm.view",)
        return ("rcm.manage",)

    @extend_schema(responses=PayerSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = Payer.objects.filter(clinic=clinic).order_by("name", "id")
        active = (request.query_params.get("is_active") or "").strip().lower()
        if active in {"1", "true"}:
            queryset = queryset.filter(is_active=True)
        elif active in {"0", "false"}:
            queryset = queryset.filter(is_active=False)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(PayerSerializer(page, many=True).data)

    @extend_schema(request=PayerSerializer, responses={201: PayerSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = PayerSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        payer = Payer.all_objects.create(clinic=clinic, **serializer.validated_data)
        record_audit(
            "rcm.payer_created",
            entity=payer,
            after={"name": payer.name, "code": payer.code, "is_active": payer.is_active},
            actor=request.user,
            request=request,
        )
        return Response(PayerSerializer(payer).data, status=status.HTTP_201_CREATED)


class InsurancePolicyListCreateView(RcmScopedView):
    allowed_query_params = {"page", "page_size", "patient", "payer", "is_active"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("rcm.view",)
        return ("rcm.manage",)

    @extend_schema(responses=InsurancePolicySerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = (
            InsurancePolicy.objects.filter(clinic=clinic)
            .select_related("payer")
            .order_by("-valid_from", "-id")
        )
        for field in ("patient", "payer"):
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
            InsurancePolicySerializer(page, many=True).data
        )

    @extend_schema(
        request=InsurancePolicySerializer, responses={201: InsurancePolicySerializer}
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = InsurancePolicySerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        policy = InsurancePolicy.all_objects.create(
            clinic=clinic, **serializer.validated_data
        )
        record_audit(
            "rcm.policy_created",
            entity=policy,
            after={
                "patient_id": str(policy.patient_id),
                "payer_id": str(policy.payer_id),
                "valid_from": policy.valid_from.isoformat(),
                "valid_to": policy.valid_to.isoformat() if policy.valid_to else None,
                "is_active": policy.is_active,
            },
            actor=request.user,
            request=request,
        )
        return Response(
            InsurancePolicySerializer(policy).data, status=status.HTTP_201_CREATED
        )


class EligibilityCheckCreateView(RcmScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("rcm.manage",)

    @extend_schema(
        request=EligibilityCheckCreateSerializer,
        responses={201: EligibilityCheckSerializer},
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = EligibilityCheckCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        policy = InsurancePolicy.all_objects.get(pk=data["policy"])
        record = eligibility_service.check(
            policy,
            data["service_date"],
            actor=request.user,
            request=request,
        )
        return Response(
            EligibilityCheckSerializer(record).data, status=status.HTTP_201_CREATED
        )


# --------------------------------------------------------------------------- #
# Claims
# --------------------------------------------------------------------------- #


class ClaimListCreateView(RcmScopedView):
    allowed_query_params = {"page", "page_size", "status", "patient"}

    def get_required_perms(self, request) -> tuple[str, ...]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return ("rcm.view",)
        return ("rcm.manage",)

    @extend_schema(responses=ClaimSerializer(many=True))
    def get(self, request):
        clinic = self.clinic(request)
        queryset = (
            Claim.objects.filter(clinic=clinic)
            .select_related("policy", "payer", "invoice")
            .prefetch_related("lines")
            .order_by("-created_at", "-id")
        )
        for field in ("status", "patient"):
            value = (request.query_params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ClaimSerializer(page, many=True).data)

    @extend_schema(request=ClaimCreateSerializer, responses={201: ClaimSerializer})
    def post(self, request):
        clinic = self.clinic(request)
        serializer = ClaimCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.all_objects.get(pk=data["patient"])
        invoice = (
            Invoice.all_objects.get(pk=data["invoice"]) if data.get("invoice") else None
        )
        policy = (
            InsurancePolicy.all_objects.get(pk=data["policy"])
            if data.get("policy")
            else None
        )
        payer = Payer.all_objects.get(pk=data["payer"]) if data.get("payer") else None

        claim = claim_service.create_claim(
            clinic,
            patient=patient,
            lines=list(data["lines"]),
            policy=policy,
            invoice=invoice,
            diagnosis_codes=list(data.get("diagnosis_codes") or []),
            payer=payer,
            actor=request.user,
            request=request,
        )
        claim = Claim.objects.select_related("policy", "payer", "invoice").get(pk=claim.pk)
        return Response(ClaimSerializer(claim).data, status=status.HTTP_201_CREATED)


class ClaimSubmitView(RcmScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("rcm.manage",)

    @extend_schema(request=None, responses=ClaimSerializer)
    def post(self, request, claim_id):
        claim = self.claim(request, claim_id)
        claim = claim_service.submit(claim, actor=request.user, request=request)
        claim = Claim.objects.select_related("policy", "payer", "invoice").get(pk=claim.pk)
        return Response(ClaimSerializer(claim).data)


class ClaimEventsView(RcmScopedView):
    allowed_query_params = {"page", "page_size"}

    @extend_schema(responses=ClaimEventSerializer(many=True))
    def get(self, request, claim_id):
        claim = self.claim(request, claim_id)
        queryset = claim.events.all().order_by("at", "id")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ClaimEventSerializer(page, many=True).data
        )


# --------------------------------------------------------------------------- #
# Remittances and reconciliation
# --------------------------------------------------------------------------- #


class RemittanceCreateView(RcmScopedView):
    def get_required_perms(self, request) -> tuple[str, ...]:
        return ("rcm.manage",)

    @extend_schema(
        request=RemittanceCreateSerializer, responses={201: RemittanceSerializer}
    )
    def post(self, request):
        clinic = self.clinic(request)
        serializer = RemittanceCreateSerializer(data=request.data, context={"clinic": clinic})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        claim = Claim.all_objects.get(pk=data["claim"])
        remittance = remittance_service.post(
            clinic,
            claim=claim,
            payer_ref=data["payer_ref"],
            paid_minor=data.get("paid_minor", 0),
            adjustments=list(data.get("adjustments") or []),
            status=data.get("status") or "posted",
            actor=request.user,
            request=request,
        )
        return Response(
            RemittanceSerializer(remittance).data, status=status.HTTP_201_CREATED
        )


class ReconciliationView(RcmScopedView):
    allowed_query_params: set[str] = set()

    @extend_schema(responses=ReconciliationSerializer)
    def get(self, request):
        clinic = self.clinic(request)
        return Response(reconcile_service.reconciliation(clinic))


__all__ = [
    "ClaimEventsView",
    "ClaimListCreateView",
    "ClaimSubmitView",
    "EligibilityCheckCreateView",
    "InsurancePolicyListCreateView",
    "PayerListCreateView",
    "ReconciliationView",
    "RemittanceCreateView",
    "rcm_enabled",
]
