"""Grounded answer and citation tests (T125, F-13)."""

from __future__ import annotations

import pytest

from apps.ai.providers.base import AIResponse, Usage
from apps.ai.rag.answer import (
    REFUSAL_TEXT,
    answer_question,
    build_grounded_messages,
    citations_for_answer,
    cited_indexes,
)
from apps.ai.rag.ingest import archive_document, ingest_document
from apps.ai.rag.retrieve import search

CHAT_URL = "/api/v1/ai/chat/"

POLICY = (
    "Enamel Dental is open Monday to Friday from 9:00 to 18:00 and Saturday from "
    "9:00 to 14:00. The clinic is closed on Sundays and public holidays."
)


class CitingProvider:
    """A provider stub that answers with an exact set of citation markers."""

    name = "citing-stub"
    model = "citing-stub"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def complete(self, messages, *, tools=None, response_schema=None, timeout=None):
        return AIResponse(
            text=self.answer,
            model=self.model,
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            tool_calls=[],
            finish_reason="stop",
            provider=self.name,
        )

    def embed(self, texts):
        from apps.ai.providers.factory import get_provider

        # The query is embedded with the same configured provider, so the
        # vector dimension matches the pgvector column.
        return get_provider().embed(texts)

    def health(self):
        return {"provider": self.name, "configured": True}


def test_citations_are_a_subset_of_retrieved_chunks(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")

    answer = answer_question(clinic, "what are your opening hours?")

    assert answer.refused is False
    assert answer.citations
    retrieved_ids = {chunk.chunk_id for chunk in answer.retrieved}
    citation_ids = {citation["chunk_id"] for citation in answer.citations}
    assert citation_ids <= retrieved_ids
    assert citation_ids == retrieved_ids
    for citation in answer.citations:
        assert set(citation) == {"chunk_id", "document_id", "title", "score"}


def test_only_the_cited_chunks_become_citations(clinic_factory):
    """A citation must be a chunk the answer actually used (F-13)."""

    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")
    ingest_document(clinic, title="Parking", text="Parking is behind the building.")

    answer = answer_question(
        clinic,
        "what are your opening hours?",
        provider=CitingProvider("The clinic is open from 9:00 [2]."),
    )

    assert answer.refused is False
    assert answer.citations, "a cited answer must produce citations"
    retrieved = answer.retrieved
    assert len(retrieved) >= 2
    # The marker points at the second context entry, so exactly that chunk is
    # cited - not the whole retrieved set.
    assert [citation["chunk_id"] for citation in answer.citations] == [retrieved[1].chunk_id]
    citation_ids = {citation["chunk_id"] for citation in answer.citations}
    assert citation_ids <= {chunk.chunk_id for chunk in answer.retrieved}


def test_an_answer_without_a_marker_has_no_citations(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")

    answer = answer_question(
        clinic,
        "what are your opening hours?",
        provider=CitingProvider("The clinic is open from 9:00."),
    )

    assert answer.refused is False
    assert answer.citations == []


def test_a_marker_outside_the_context_is_ignored(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")

    answer = answer_question(
        clinic,
        "what are your opening hours?",
        provider=CitingProvider("Open from 9:00 [99]."),
    )

    assert answer.citations == []


def test_cited_indexes_ignores_out_of_range_and_repeats():
    assert cited_indexes("no markers here", 3) == []
    assert cited_indexes("text [2] more [2] and [1]", 3) == [2, 1]
    assert cited_indexes("text [0]", 3) == []
    assert cited_indexes("text [4]", 3) == []
    assert cited_indexes("text [12]", 3) == []


def test_citations_for_answer_maps_only_cited_chunks(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")
    chunks = search(clinic, "opening hours", top_k=3)

    citations = citations_for_answer("Supported by [1].", chunks)

    assert [citation["chunk_id"] for citation in citations] == [chunks[0].chunk_id]
    assert set(citations[0]) == {"chunk_id", "document_id", "title", "score"}


def test_below_threshold_query_refuses_with_fixed_string(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY)

    answer = answer_question(clinic, "opening hours", min_score=1.5)

    assert answer.refused is True
    assert answer.answer == REFUSAL_TEXT
    assert answer.citations == []


def test_empty_knowledge_base_refuses(clinic_factory):
    clinic = clinic_factory()
    answer = answer_question(clinic, "what are your opening hours?")
    assert answer.refused is True
    assert answer.answer == REFUSAL_TEXT


def test_archived_document_is_absent_from_retrieval(clinic_factory):
    clinic = clinic_factory()
    result = ingest_document(clinic, title="Hours", text=POLICY)

    assert search(clinic, "opening hours", top_k=5)

    archive_document(result.document)
    assert search(clinic, "opening hours", top_k=5) == []


def test_context_block_is_wrapped_as_data(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY)
    chunks = search(clinic, "opening hours", top_k=3)

    messages = build_grounded_messages("opening hours?", chunks)

    assert messages[0].role == "system"
    assert "untrusted data" in messages[0].content
    assert "<context>" in messages[1].content
    assert "</context>" in messages[1].content
    # The prompt asks for numbered citations, and the context is numbered.
    assert "[" in messages[0].content
    assert "[1] source:" in messages[1].content


def test_chat_with_use_knowledge_returns_citations(owner):
    ingest_document(owner.clinic, title="Hours", text=POLICY, kind="policy")

    response = owner.post(
        CHAT_URL, {"message": "what are your opening hours?", "use_knowledge": True}
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["refused"] is False
    assert body["citations"]
    retrieved_ids = {chunk.chunk_id for chunk in search(owner.clinic, "opening hours", top_k=5)}
    assert {citation["chunk_id"] for citation in body["citations"]} <= retrieved_ids


def test_chat_with_use_knowledge_refuses_without_knowledge(owner):
    response = owner.post(
        CHAT_URL, {"message": "what are your opening hours?", "use_knowledge": True}
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["refused"] is True
    assert body["answer"] == REFUSAL_TEXT
    assert body["citations"] == []


@pytest.mark.parametrize("marker", ["[1]", "[2]", "[3]"])
def test_the_mock_provider_marks_the_context_it_used(clinic_factory, marker):
    """The deterministic provider has to honour the citation contract."""

    clinic = clinic_factory()
    ingest_document(clinic, title="Hours", text=POLICY, kind="policy")

    answer = answer_question(clinic, "what are your opening hours?")

    assert answer.refused is False
    assert answer.citations
    assert any(marker in answer.answer for marker in ("[1]", "[2]", "[3]"))
