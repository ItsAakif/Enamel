"""AI URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .views import (
    CallDetailView,
    CallEscalateView,
    CallListCreateView,
    CallToolInvokeView,
    CallTurnCreateView,
    ChatView,
    InvocationListView,
    KnowledgeDocumentDetailView,
    KnowledgeDocumentListCreateView,
    KnowledgeSearchView,
    PromptListView,
)

app_name = "ai"

urlpatterns = [
    path("ai/chat/", ChatView.as_view(), name="chat"),
    path("ai/invocations/", InvocationListView.as_view(), name="invocation-list"),
    path("ai/prompts/", PromptListView.as_view(), name="prompt-list"),
    path(
        "ai/knowledge/documents/",
        KnowledgeDocumentListCreateView.as_view(),
        name="knowledge-document-list",
    ),
    path(
        "ai/knowledge/documents/<uuid:document_id>/",
        KnowledgeDocumentDetailView.as_view(),
        name="knowledge-document-detail",
    ),
    path(
        "ai/knowledge/search/",
        KnowledgeSearchView.as_view(),
        name="knowledge-search",
    ),
    path("ai/calls/", CallListCreateView.as_view(), name="call-list"),
    path("ai/calls/<uuid:call_id>/", CallDetailView.as_view(), name="call-detail"),
    path("ai/calls/<uuid:call_id>/turns/", CallTurnCreateView.as_view(), name="call-turn"),
    path(
        "ai/calls/<uuid:call_id>/tools/<str:tool>/",
        CallToolInvokeView.as_view(),
        name="call-tool",
    ),
    path(
        "ai/calls/<uuid:call_id>/escalate/",
        CallEscalateView.as_view(),
        name="call-escalate",
    ),
]
