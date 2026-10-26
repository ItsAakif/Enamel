"""Scheduling URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    AppointmentActionView,
    AppointmentDetailView,
    AppointmentListCreateView,
    AppointmentRescheduleView,
    AppointmentTypeDetailView,
    AppointmentTypeListCreateView,
    AvailabilityRuleDetailView,
    AvailabilityRuleListCreateView,
    AvailabilityView,
    GapsView,
    OfferClaimView,
    OfferDeclineView,
    OperatoryDetailView,
    OperatoryListCreateView,
    ProviderDetailView,
    ProviderListCreateView,
    RecommendationsView,
    ScheduleExceptionDetailView,
    ScheduleExceptionListCreateView,
    SchedulingMetricsView,
    WaitlistDetailView,
    WaitlistListCreateView,
    WaitlistOfferCreateView,
)

app_name = "scheduling"

urlpatterns = [
    path(
        "appointment-types/",
        AppointmentTypeListCreateView.as_view(),
        name="appointment-type-list",
    ),
    path(
        "appointment-types/<uuid:type_id>/",
        AppointmentTypeDetailView.as_view(),
        name="appointment-type-detail",
    ),
    path("providers/", ProviderListCreateView.as_view(), name="provider-list"),
    path(
        "providers/<uuid:provider_id>/",
        ProviderDetailView.as_view(),
        name="provider-detail",
    ),
    path("operatories/", OperatoryListCreateView.as_view(), name="operatory-list"),
    path(
        "operatories/<uuid:operatory_id>/",
        OperatoryDetailView.as_view(),
        name="operatory-detail",
    ),
    path(
        "availability-rules/",
        AvailabilityRuleListCreateView.as_view(),
        name="availability-rule-list",
    ),
    path(
        "availability-rules/<uuid:rule_id>/",
        AvailabilityRuleDetailView.as_view(),
        name="availability-rule-detail",
    ),
    path(
        "exceptions/",
        ScheduleExceptionListCreateView.as_view(),
        name="schedule-exception-list",
    ),
    path(
        "exceptions/<uuid:exception_id>/",
        ScheduleExceptionDetailView.as_view(),
        name="schedule-exception-detail",
    ),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("appointments/", AppointmentListCreateView.as_view(), name="appointment-list"),
    path(
        "appointments/<uuid:appointment_id>/",
        AppointmentDetailView.as_view(),
        name="appointment-detail",
    ),
    path(
        "appointments/<uuid:appointment_id>/reschedule/",
        AppointmentRescheduleView.as_view(),
        name="appointment-reschedule",
    ),
    path(
        "appointments/<uuid:appointment_id>/<str:action>/",
        AppointmentActionView.as_view(),
        name="appointment-action",
    ),
    path(
        "scheduling/metrics/",
        SchedulingMetricsView.as_view(),
        name="scheduling-metrics",
    ),
    path("waitlist/", WaitlistListCreateView.as_view(), name="waitlist-list"),
    path(
        "waitlist/<uuid:entry_id>/",
        WaitlistDetailView.as_view(),
        name="waitlist-detail",
    ),
    path(
        "waitlist/<uuid:entry_id>/offers/",
        WaitlistOfferCreateView.as_view(),
        name="waitlist-offer-create",
    ),
    path(
        "offers/<str:token>/claim/",
        OfferClaimView.as_view(),
        name="offer-claim",
    ),
    path(
        "offers/<str:token>/decline/",
        OfferDeclineView.as_view(),
        name="offer-decline",
    ),
    path(
        "scheduling/recommendations/",
        RecommendationsView.as_view(),
        name="scheduling-recommendations",
    ),
    path("scheduling/gaps/", GapsView.as_view(), name="scheduling-gaps"),
]
