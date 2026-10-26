"""RCM URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    ClaimEventsView,
    ClaimListCreateView,
    ClaimSubmitView,
    EligibilityCheckCreateView,
    InsurancePolicyListCreateView,
    PayerListCreateView,
    ReconciliationView,
    RemittanceCreateView,
)

app_name = "rcm"

urlpatterns = [
    path("payers/", PayerListCreateView.as_view(), name="payer-list"),
    path(
        "insurance-policies/",
        InsurancePolicyListCreateView.as_view(),
        name="insurance-policy-list",
    ),
    path(
        "eligibility-checks/",
        EligibilityCheckCreateView.as_view(),
        name="eligibility-check-create",
    ),
    path("claims/", ClaimListCreateView.as_view(), name="claim-list"),
    path(
        "claims/<uuid:claim_id>/submit/",
        ClaimSubmitView.as_view(),
        name="claim-submit",
    ),
    path(
        "claims/<uuid:claim_id>/events/",
        ClaimEventsView.as_view(),
        name="claim-events",
    ),
    path("remittances/", RemittanceCreateView.as_view(), name="remittance-create"),
    path(
        "rcm/reconciliation/",
        ReconciliationView.as_view(),
        name="reconciliation",
    ),
]
