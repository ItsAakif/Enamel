"""Clinic-scoped retrieval tests (T125): ordering, top_k, logging."""

from __future__ import annotations

from django.conf import settings

from apps.ai.models import KnowledgeChunk, KnowledgeDocument, RetrievalLog
from apps.ai.rag.ingest import ingest_document
from apps.ai.rag.retrieve import search, search_by_vector

DIM = settings.AI_EMBEDDING_DIM


def unit_vector(head: list[float]) -> list[float]:
    """A ``DIM``-width vector whose first components are ``head``."""

    return list(head) + [0.0] * (DIM - len(head))


def make_document(clinic, *, title: str = "Doc", sha: str = "a" * 64) -> KnowledgeDocument:
    return KnowledgeDocument.all_objects.create(
        clinic=clinic, title=title, sha256=sha, chunk_count=0
    )


def make_chunk(clinic, document, ordinal: int, head: list[float]) -> KnowledgeChunk:
    return KnowledgeChunk.all_objects.create(
        clinic=clinic,
        document=document,
        ordinal=ordinal,
        text=f"chunk-{ordinal}",
        token_count=1,
        embedding=unit_vector(head),
    )


def test_search_orders_by_cosine_similarity(clinic_factory):
    clinic = clinic_factory()
    document = make_document(clinic)
    make_chunk(clinic, document, 0, [1.0, 0.0])
    make_chunk(clinic, document, 1, [0.9, 0.1])
    make_chunk(clinic, document, 2, [0.0, 1.0])

    results = search_by_vector(clinic, unit_vector([1.0, 0.0]), top_k=3, log=False)

    assert [item.ordinal for item in results] == [0, 1, 2]
    scores = [item.score for item in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0


def test_search_respects_top_k(clinic_factory):
    clinic = clinic_factory()
    document = make_document(clinic)
    for ordinal in range(5):
        make_chunk(clinic, document, ordinal, [1.0, 0.0])

    results = search_by_vector(clinic, unit_vector([1.0, 0.0]), top_k=2, log=False)
    assert len(results) == 2


def test_search_is_clinic_scoped(clinic_factory):
    clinic_a = clinic_factory(slug="clinic-a")
    clinic_b = clinic_factory(slug="clinic-b")
    doc_a = ingest_document(clinic_a, title="A", text="Clinic A is open on weekdays.")
    ingest_document(clinic_b, title="B", text="Clinic A is open on weekdays.")

    results = search(clinic_a, "when is the clinic open?", top_k=20)

    assert results
    assert {item.document_id for item in results} == {str(doc_a.document.id)}


def test_search_writes_a_content_free_retrieval_log(clinic_factory):
    clinic = clinic_factory()
    ingest_document(clinic, title="A", text="Clinic A is open on weekdays.")

    results = search(clinic, "opening hours", top_k=5)

    log = RetrievalLog.all_objects.filter(clinic=clinic).latest("created_at")
    assert log.result_count == len(results)
    assert log.top_k == 5
    assert log.query_hash
    assert log.best_score is not None
    assert log.latency_ms >= 0

    field_names = {field.name for field in RetrievalLog._meta.get_fields()}
    assert "query" not in field_names
    assert "query_text" not in field_names
    assert "text" not in field_names


def test_empty_clinic_returns_no_results(clinic_factory):
    clinic = clinic_factory()
    assert search(clinic, "anything", top_k=5) == []
