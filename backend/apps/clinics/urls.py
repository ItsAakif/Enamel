"""Clinic URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    ClinicAuditView,
    ClinicDetailView,
    ClinicListCreateView,
    InvitationAcceptView,
    InvitationListCreateView,
    InvitationRevokeView,
    LocationDetailView,
    LocationListCreateView,
    MemberDetailView,
    MemberListView,
)

app_name = "clinics"

urlpatterns = [
    path("clinics/", ClinicListCreateView.as_view(), name="clinic-list"),
    path("clinics/<uuid:clinic_id>/", ClinicDetailView.as_view(), name="clinic-detail"),
    path(
        "clinics/<uuid:clinic_id>/locations/",
        LocationListCreateView.as_view(),
        name="location-list",
    ),
    path(
        "clinics/<uuid:clinic_id>/locations/<uuid:location_id>/",
        LocationDetailView.as_view(),
        name="location-detail",
    ),
    path(
        "clinics/<uuid:clinic_id>/members/",
        MemberListView.as_view(),
        name="member-list",
    ),
    path(
        "clinics/<uuid:clinic_id>/members/<uuid:membership_id>/",
        MemberDetailView.as_view(),
        name="member-detail",
    ),
    path(
        "clinics/<uuid:clinic_id>/invitations/",
        InvitationListCreateView.as_view(),
        name="invitation-list-create",
    ),
    path(
        "clinics/<uuid:clinic_id>/invitations/<uuid:invitation_id>/revoke/",
        InvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path(
        "invitations/<str:token>/accept/",
        InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path(
        "clinics/<uuid:clinic_id>/audit/",
        ClinicAuditView.as_view(),
        name="clinic-audit",
    ),
]
