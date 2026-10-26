"""AI API tests (T123): chat, invocations and prompt metadata."""

from __future__ import annotations

from apps.ai.models import AIInvocation
from apps.ai.prompts.registry import get_prompt

CHAT_URL = "/api/v1/ai/chat/"
INVOCATIONS_URL = "/api/v1/ai/invocations/"
PROMPTS_URL = "/api/v1/ai/prompts/"


def test_chat_requires_authentication(api_client):
    response = api_client.post(CHAT_URL, {"message": "hello"}, format="json")
    assert response.status_code == 401


def test_chat_returns_answer_and_usage(owner, settings):
    settings.AI_PROVIDER = "mock"
    response = owner.post(CHAT_URL, {"message": "What are your opening hours?"})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["answer"]
    assert body["degraded"] is False
    assert body["model"] == "mock-deterministic"
    assert body["usage"]["total_tokens"] > 0
    assert body["citations"] == []


def test_chat_blank_message_is_rejected(owner):
    response = owner.post(CHAT_URL, {"message": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_chat_records_one_invocation(owner):
    owner.post(CHAT_URL, {"message": "hello"})
    rows = AIInvocation.objects.filter(clinic=owner.clinic)
    assert rows.count() == 1
    row = rows.first()
    assert row.request_hash and row.response_hash
    assert row.prompt_key == "assistant.chat"
    assert row.trace_id


def test_chat_budget_exceeded(owner, settings):
    settings.AI_MONTHLY_BUDGET_MINOR = 10
    AIInvocation.objects.create(
        clinic=owner.clinic,
        provider="mock",
        model="mock-deterministic",
        status=AIInvocation.Status.OK,
        cost_minor=10,
    )
    response = owner.post(CHAT_URL, {"message": "hello"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "budget_exceeded"


def test_invocations_list_owner_only(owner, assistant):
    owner.post(CHAT_URL, {"message": "hello"})
    allowed = owner.get(INVOCATIONS_URL)
    assert allowed.status_code == 200
    assert allowed.json()["count"] == 1

    denied = assistant.get(INVOCATIONS_URL)
    assert denied.status_code == 403


def test_prompts_list_is_metadata_only(owner, dentist):
    response = owner.get(PROMPTS_URL)
    assert response.status_code == 200
    payload = response.json()
    keys = {row["key"] for row in payload}
    assert "assistant.chat" in keys
    for row in payload:
        assert "body" not in row
        assert set(row) == {"key", "version", "purpose", "schema_json", "is_active"}

    # dentist lacks ai.configure
    assert dentist.get(PROMPTS_URL).status_code == 403


def test_prompt_registry_version_exposed(owner):
    owner.get(PROMPTS_URL)
    response = owner.get(PROMPTS_URL)
    assistant_row = next(r for r in response.json() if r["key"] == "assistant.chat")
    assert assistant_row["version"] == get_prompt("assistant.chat").version
