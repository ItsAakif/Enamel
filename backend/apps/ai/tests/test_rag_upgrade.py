"""Retrieval upgrade tests: metrics, dataset integrity, hybrid fusion, rerank."""

from __future__ import annotations

import pytest

from apps.ai.evals.rag_eval import load_corpus, load_questions
from apps.ai.evals.rag_metrics import aggregate, ndcg_at_k, recall_at_k, reciprocal_rank
from apps.ai.providers.embeddings import EmbeddingConfigurationError, get_embedding_provider
from apps.ai.rag.retrieve import RetrievedChunk, search_hybrid


def _chunk(ordinal: int, text: str, score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"chunk-{ordinal}",
        document_id=f"doc-{ordinal}",
        ordinal=ordinal,
        text=text,
        token_count=10,
        title="T",
        kind="policy",
        score=score,
    )


def test_recall_and_rank_metrics():
    ranked = ["a", "b", "c"]
    relevant = {"c"}
    assert recall_at_k(ranked, relevant, 3) == 1.0
    assert recall_at_k(ranked, relevant, 2) == 0.0
    assert reciprocal_rank(ranked, relevant, 3) == pytest.approx(1 / 3)
    assert 0.0 < ndcg_at_k(ranked, relevant, 3) < 1.0


def test_aggregate_ignores_questions_without_relevant_ids():
    summary = aggregate(
        [(["a"], {"a"}), (["b"], set())],
        k=1,
    )
    assert summary["questions"] == 1
    assert summary["recall@1"] == 1.0


def test_dataset_is_consistent():
    corpus = load_corpus()
    questions = load_questions()
    slugs = {doc["slug"] for doc in corpus}
    assert len(corpus) >= 12
    assert len(questions) >= 30
    assert len({q["id"] for q in questions}) == len(questions)
    for question in questions:
        assert set(question["relevant"]) <= slugs
        if question["category"] == "unanswerable":
            assert question["relevant"] == []


def test_hybrid_fusion_merges_and_orders(monkeypatch):
    vector_hits = [_chunk(0, "vector first"), _chunk(1, "shared"), _chunk(2, "vector third")]
    lexical_hits = [_chunk(1, "shared"), _chunk(3, "lexical second")]

    monkeypatch.setattr(
        "apps.ai.rag.retrieve.search_by_vector", lambda *a, **k: vector_hits
    )
    monkeypatch.setattr(
        "apps.ai.rag.lexical.search_lexical", lambda *a, **k: lexical_hits
    )
    monkeypatch.setattr("apps.ai.rag.retrieve.embed_text", lambda *a, **k: [0.0])

    fused = search_hybrid(
        object(), "q", top_k=4, log=False, rerank=False, candidates=10, rrf_k=60
    )
    ids = [hit.chunk_id for hit in fused]
    assert "chunk-1" in ids
    assert ids[0] == "chunk-1"  # appears in both lists, so it wins the fusion
    assert len(ids) == 4


def test_lexical_rerank_is_deterministic():
    from apps.ai.rag.rerank import lexical_rerank

    chunks = [_chunk(0, "unrelated text"), _chunk(1, "parking is behind the building")]
    first = lexical_rerank("where is the parking", chunks, 2)
    second = lexical_rerank("where is the parking", chunks, 2)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert first[0].chunk_id == "chunk-1"


def test_embedding_provider_factory():
    assert get_embedding_provider("mock").__class__.__name__ == "ChatProviderEmbeddings"
    with pytest.raises(EmbeddingConfigurationError):
        get_embedding_provider("does-not-exist")
