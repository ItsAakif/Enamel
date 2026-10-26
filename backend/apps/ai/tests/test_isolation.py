"""Cross-clinic leakage tests (T125): retrieval must never cross tenants."""

from __future__ import annotations

from django.conf import settings

from apps.ai.models import KnowledgeChunk, KnowledgeDocument
from apps.ai.rag.ingest import ingest_document
from apps.ai.rag.retrieve import search, search_by_vector

DIM = settings.AI_EMBEDDING_DIM

SHARED_TEXT = (
    "Enamel Dental is open Monday to Friday from 9:00 to 18:00. "
    "We accept most insurance plans and offer payment plans. " * 30
)


def unit_vector(head: list[float]) -> list[float]:
    return list(head) + [0.0] * (DIM - len(head))


def test_identical_text_in_two_clinics_never_leaks(clinic_factory):
    clinic_a = clinic_factory(slug="iso-a")
    clinic_b = clinic_factory(slug="iso-b")
    doc_a = ingest_document(clinic_a, title="Shared", text=SHARED_TEXT)
    doc_b = ingest_document(clinic_b, title="Shared", text=SHARED_TEXT)

    results = search(clinic_a, "opening hours and insurance", top_k=50)

    assert results
    returned_docs = {item.document_id for item in results}
    assert returned_docs == {str(doc_a.document.id)}
    assert str(doc_b.document.id) not in returned_docs


def test_higher_scoring_cross_clinic_row_is_never_returned(clinic_factory):
    clinic_a = clinic_factory(slug="score-a")
    clinic_b = clinic_factory(slug="score-b")

    doc_a = KnowledgeDocument.all_objects.create(
        clinic=clinic_a, title="A", sha256="a" * 64, chunk_count=1
    )
    doc_b = KnowledgeDocument.all_objects.create(
        clinic=clinic_b, title="B", sha256="b" * 64, chunk_count=1
    )
    # Clinic A's chunk is a weaker match; clinic B's chunk is an exact match.
    KnowledgeChunk.all_objects.create(
        clinic=clinic_a,
        document=doc_a,
        ordinal=0,
        text="weak",
        token_count=1,
        embedding=unit_vector([0.2, 0.98]),
    )
    KnowledgeChunk.all_objects.create(
        clinic=clinic_b,
        document=doc_b,
        ordinal=0,
        text="exact",
        token_count=1,
        embedding=unit_vector([1.0, 0.0]),
    )

    results = search_by_vector(clinic_a, unit_vector([1.0, 0.0]), top_k=10, log=False)

    assert [item.document_id for item in results] == [str(doc_a.id)]


def test_search_without_matching_clinic_returns_nothing(clinic_factory):
    clinic_a = clinic_factory(slug="empty-a")
    clinic_b = clinic_factory(slug="empty-b")
    ingest_document(clinic_b, title="B", text=SHARED_TEXT)

    assert search(clinic_a, "opening hours", top_k=10) == []
