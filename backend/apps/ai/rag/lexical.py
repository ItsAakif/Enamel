"""Lexical (PostgreSQL full-text) retrieval for hybrid search.

The tenant filter is applied before ranking, exactly like the vector path: a
chunk from another clinic is never a candidate. The score is ``ts_rank_cd``
normalised to 0..1 by the best hit in the result window, which keeps the value
interpretable in a report; hybrid fusion uses ranks, not this score.
"""

from __future__ import annotations

import time
from typing import Any

from django.db import connection

from .retrieve import RetrievedChunk

LEXICAL_SQL = """
    SELECT c.id           AS chunk_id,
           c.document_id  AS document_id,
           c.ordinal      AS ordinal,
           c.text         AS text,
           c.token_count  AS token_count,
           d.title        AS title,
           d.kind         AS kind,
           ts_rank_cd(to_tsvector('english', c.text), plainto_tsquery('english', %s)) AS rank
      FROM ai_knowledgechunk c
      JOIN ai_knowledgedocument d ON d.id = c.document_id
     WHERE c.clinic_id = %s
       AND c.status = 'active'
       AND d.status = 'active'
       AND to_tsvector('english', c.text) @@ plainto_tsquery('english', %s)
     ORDER BY rank DESC, c.ordinal ASC
     LIMIT %s
"""


def search_lexical(
    clinic: Any,
    query: str,
    *,
    top_k: int = 30,
) -> list[RetrievedChunk]:
    """Return the clinic's chunks matching ``query`` by full-text rank."""

    text = str(query or "").strip()
    if not text:
        return []
    clinic_id = getattr(clinic, "id", clinic)
    limit = max(1, int(top_k))

    with connection.cursor() as cursor:
        cursor.execute(LEXICAL_SQL, [text, clinic_id, text, limit])
        rows = cursor.fetchall()

    if not rows:
        return []
    best = max(float(row[7] or 0.0) for row in rows) or 1.0
    results: list[RetrievedChunk] = []
    for row in rows:
        rank = float(row[7] or 0.0)
        results.append(
            RetrievedChunk(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                ordinal=int(row[2]),
                text=row[3] or "",
                token_count=int(row[4] or 0),
                title=row[5] or "",
                kind=row[6] or "",
                score=max(0.0, min(1.0, rank / best)),
            )
        )
    return results


def lexical_latency_ms(clinic: Any, query: str, *, top_k: int = 30) -> tuple[list[RetrievedChunk], int]:
    """Convenience wrapper used by the evaluation CLI to record latency."""

    started = time.perf_counter()
    results = search_lexical(clinic, query, top_k=top_k)
    return results, int((time.perf_counter() - started) * 1000)


__all__ = ["LEXICAL_SQL", "lexical_latency_ms", "search_lexical"]
