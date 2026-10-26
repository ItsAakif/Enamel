"""Clinic-scoped cosine retrieval (T125).

The one rule that matters: the tenant filter is applied **before** the vector
ordering. The raw SQL below pins ``WHERE c.clinic_id = %s AND c.status =
'active' AND d.status = 'active'`` ahead of ``ORDER BY embedding <=> %s``, so a
high-scoring row from another clinic can never be returned -- the database never
sees it as a candidate. ``RetrievalLog`` records metrics and a hash of the query,
never the query text.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.conf import settings
from django.db import connection

from ..providers.base import AIProvider
from .chunk import normalize_text
from .embed import embed_text

RETRIEVAL_SQL = """
    SELECT c.id           AS chunk_id,
           c.document_id  AS document_id,
           c.ordinal      AS ordinal,
           c.text         AS text,
           c.token_count  AS token_count,
           d.title        AS title,
           d.kind         AS kind,
           1 - (c.embedding <=> %s::vector) AS score
      FROM ai_knowledgechunk c
      JOIN ai_knowledgedocument d ON d.id = c.document_id
     WHERE c.clinic_id = %s
       AND c.status = 'active'
       AND d.status = 'active'
     ORDER BY c.embedding <=> %s::vector ASC, c.ordinal ASC
     LIMIT %s
"""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    ordinal: int
    text: str
    token_count: int
    title: str
    kind: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "ordinal": self.ordinal,
            "text": self.text,
            "token_count": self.token_count,
            "title": self.title,
            "kind": self.kind,
            "score": round(float(self.score), 6),
        }


def vector_literal(vector: Sequence[float]) -> str:
    """Render a vector as a pgvector text literal for a ``::vector`` cast."""

    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def _default_top_k() -> int:
    return int(getattr(settings, "RAG_TOP_K", 5) or 5)


def query_hash(query: str) -> str:
    return hashlib.sha256(normalize_text(query).encode("utf-8")).hexdigest()


def search_by_vector(
    clinic: Any,
    vector: Sequence[float],
    *,
    top_k: int | None = None,
    query_hash_value: str = "",
    user: Any = None,
    log: bool = True,
) -> list[RetrievedChunk]:
    """Rank active chunks for ``clinic`` by cosine similarity to ``vector``."""

    from ..models import RetrievalLog

    top_k = int(top_k or _default_top_k())
    if top_k <= 0:
        top_k = _default_top_k()
    literal = vector_literal(vector)
    clinic_id = getattr(clinic, "id", clinic)

    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(RETRIEVAL_SQL, [literal, clinic_id, literal, top_k])
        rows = cursor.fetchall()
    latency_ms = int((time.perf_counter() - started) * 1000)

    results = [
        RetrievedChunk(
            chunk_id=str(row[0]),
            document_id=str(row[1]),
            ordinal=int(row[2]),
            text=row[3] or "",
            token_count=int(row[4] or 0),
            title=row[5] or "",
            kind=row[6] or "",
            score=float(row[7]),
        )
        for row in rows
    ]

    if log:
        best = max((item.score for item in results), default=None)
        RetrievalLog.all_objects.create(
            clinic=clinic,
            user=user,
            query_hash=query_hash_value or "",
            top_k=top_k,
            result_count=len(results),
            best_score=best,
            latency_ms=latency_ms,
        )

    return results


def best_vector_score(
    clinic: Any,
    query: str,
    *,
    embedding_provider: Any = None,
) -> float | None:
    """Best cosine score for the refusal gate, without writing a log row."""

    hits = search_by_vector(
        clinic,
        embed_text(query, provider=embedding_provider),
        top_k=1,
        log=False,
    )
    return max((hit.score for hit in hits), default=None)


def _write_log(
    clinic: Any,
    *,
    query: str,
    top_k: int,
    results: list[RetrievedChunk],
    user: Any,
    latency_ms: int,
) -> None:
    from ..models import RetrievalLog

    best = max((item.score for item in results), default=None)
    RetrievalLog.all_objects.create(
        clinic=clinic,
        user=user,
        query_hash=query_hash(query),
        top_k=top_k,
        result_count=len(results),
        best_score=best,
        latency_ms=latency_ms,
    )


def search_hybrid(
    clinic: Any,
    query: str,
    *,
    top_k: int | None = None,
    embedding_provider: Any = None,
    user: Any = None,
    log: bool = True,
    candidates: int | None = None,
    rrf_k: int | None = None,
    rerank: bool | None = None,
) -> list[RetrievedChunk]:
    """Cosine + full-text retrieval fused with reciprocal rank fusion.

    Both candidate lists are clinic-scoped before ranking. The fused list is
    optionally reranked by a local cross-encoder; the returned ``score`` is the
    RRF score (or the reranker score) and is not comparable to a cosine score.
    """

    from django.conf import settings as django_settings

    from .lexical import search_lexical
    from .rerank import rerank_chunks

    started = time.perf_counter()
    limit = int(top_k or _default_top_k())
    candidate_k = max(limit, int(candidates or getattr(django_settings, "RAG_HYBRID_CANDIDATES", 30)))
    fusion_k = int(rrf_k or getattr(django_settings, "RAG_RRF_K", 60))
    do_rerank = (
        bool(getattr(django_settings, "RAG_RERANK", True)) if rerank is None else bool(rerank)
    )

    vector_hits = search_by_vector(
        clinic,
        embed_text(query, provider=embedding_provider),
        top_k=candidate_k,
        log=False,
    )
    lexical_hits = search_lexical(clinic, query, top_k=candidate_k)

    fused: dict[str, tuple[float, RetrievedChunk]] = {}
    for hits in (vector_hits, lexical_hits):
        for rank, hit in enumerate(hits):
            contribution = 1.0 / (fusion_k + rank + 1)
            previous = fused.get(hit.chunk_id)
            if previous is None:
                fused[hit.chunk_id] = (contribution, hit)
            else:
                fused[hit.chunk_id] = (previous[0] + contribution, previous[1])

    ordered = [
        replace(hit, score=round(score, 6))
        for score, hit in sorted(
            fused.values(), key=lambda item: (-item[0], item[1].ordinal)
        )
    ]
    if do_rerank and ordered:
        results = rerank_chunks(query, ordered, top_k=limit)
    else:
        results = ordered[:limit]

    if log:
        _write_log(
            clinic,
            query=query,
            top_k=limit,
            results=results,
            user=user,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    return results


def search(
    clinic: Any,
    query: str,
    *,
    top_k: int | None = None,
    provider: AIProvider | None = None,
    embedding_provider: Any = None,
    user: Any = None,
    log: bool = True,
    mode: str | None = None,
    rerank: bool | None = None,
) -> list[RetrievedChunk]:
    """Retrieve for ``clinic`` using the configured mode.

    ``provider`` is accepted for backwards compatibility with earlier callers
    and is only used when ``mode='vector'`` and no ``embedding_provider`` is
    given. The default mode comes from ``RAG_MODE`` (hybrid).
    """

    from django.conf import settings as django_settings

    active = str(mode or getattr(django_settings, "RAG_MODE", "hybrid") or "hybrid").lower()
    if active == "vector":
        vector = embed_text(query, provider=embedding_provider or provider)
        return search_by_vector(
            clinic,
            vector,
            top_k=top_k,
            query_hash_value=query_hash(query),
            user=user,
            log=log,
        )
    return search_hybrid(
        clinic,
        query,
        top_k=top_k,
        embedding_provider=embedding_provider,
        user=user,
        log=log,
        rerank=rerank,
    )


__all__ = [
    "RetrievedChunk",
    "query_hash",
    "search",
    "search_by_vector",
    "search_hybrid",
    "vector_literal",
]
