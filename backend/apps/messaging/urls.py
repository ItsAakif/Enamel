"""Messaging URL routes (mounted under /api/v1/)."""

from django.urls import path

from .views import (
    ConsentListCreateView,
    ConsentRevokeView,
    MessageListView,
    MessageTemplateDetailView,
    MessageTemplateListCreateView,
    MessagingWebhookView,
    RecallDueView,
    RecallScheduleView,
    RecallTypeListCreateView,
    SuppressionListView,
)

app_name = "messaging"

urlpatterns = [
    path("consents/", ConsentListCreateView.as_view(), name="consent-list"),
    path(
        "consents/<uuid:consent_id>/revoke/",
        ConsentRevokeView.as_view(),
        name="consent-revoke",
    ),
    path("suppressions/", SuppressionListView.as_view(), name="suppression-list"),
    path(
        "message-templates/",
        MessageTemplateListCreateView.as_view(),
        name="message-template-list",
    ),
    path(
        "message-templates/<uuid:template_id>/",
        MessageTemplateDetailView.as_view(),
        name="message-template-detail",
    ),
    path("messages/", MessageListView.as_view(), name="message-list"),
    path("recall-types/", RecallTypeListCreateView.as_view(), name="recall-type-list"),
    path("recall/due/", RecallDueView.as_view(), name="recall-due"),
    path(
        "recall/<uuid:patient_id>/schedule/",
        RecallScheduleView.as_view(),
        name="recall-schedule",
    ),
    path(
        "webhooks/messaging/<str:adapter>/",
        MessagingWebhookView.as_view(),
        name="messaging-webhook",
    ),
]
