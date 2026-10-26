"""Retrieval relevance suite (T126): recall@k and citation validity.

Each case seeds one document whose chunk embedding is *exactly* the query
embedding (a guaranteed top hit) plus two distractors, so recall@k is
well-defined and deterministic with the hash-based mock embeddings. Citation
validity asserts every returned chunk belongs to the case clinic.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from ..suites import load_dataset, result_row

SUITE = "retrieval"
TOP_K = 3


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(world: Any, provider: Any, seed: int) -> list[dict[str, Any]]:
    from apps.ai.models import KnowledgeChunk, KnowledgeDocument
    from apps.ai.rag.embed import embed_text
    from apps.ai.rag.retrieve import search

    clinic = world.clinic_r
    rows: list[dict[str, Any]] = []

    for case in load_dataset(SUITE):
        query = case["query"]
        title = case["expected_title"]

        document = KnowledgeDocument.all_objects.create(
            clinic=clinic, title=title, sha256=_sha(title), chunk_count=1
        )
        world.docs_r.append(document)
        KnowledgeChunk.all_objects.create(
            clinic=clinic,
            document=document,
            ordinal=0,
            text=f"content for {title}",
            token_count=4,
            embedding=embed_text(query, provider=provider),
        )
        for index in range(2):
            distractor_title = f"{title} distractor {index}"
            distractor = KnowledgeDocument.all_objects.create(
                clinic=clinic, title=distractor_title, sha256=_sha(distractor_title), chunk_count=1
            )
            world.docs_r.append(distractor)
            KnowledgeChunk.all_objects.create(
                clinic=clinic,
                document=distractor,
                ordinal=0,
                text="unrelated distractor content",
                token_count=3,
                embedding=embed_text(f"{query} #{index}", provider=provider),
            )

        started = time.perf_counter()
        results = search(clinic, query, top_k=TOP_K, provider=provider)
        latency_ms = int((time.perf_counter() - started) * 1000)

        returned_titles = [item.title for item in results]
        recall = 1.0 if title in returned_titles else 0.0
        valid_ids = world.doc_ids("r")
        citations_valid = bool(results) and all(
            item.document_id in valid_ids for item in results
        )
        passed = recall >= 1.0 and citations_valid
        reason = f"recall@{TOP_K}={recall} citations_valid={citations_valid}"
        tokens = sum(item.token_count for item in results)

        rows.append(
            result_row(
                SUITE, case["id"], passed, reason, latency_ms=latency_ms, tokens=tokens
            )
        )

    return rows
