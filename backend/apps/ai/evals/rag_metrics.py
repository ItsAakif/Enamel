"""Retrieval metrics: recall@k, MRR@k and nDCG@k with binary relevance.

Pure functions so the metric definitions are testable without a database.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """1.0 when at least one relevant id is in the first ``k`` results."""

    if not relevant:
        return 0.0
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def first_relevant_rank(ranked: Sequence[str], relevant: set[str], k: int) -> int | None:
    for index, item in enumerate(ranked[:k], start=1):
        if item in relevant:
            return index
    return None


def reciprocal_rank(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    rank = first_relevant_rank(ranked, relevant, k)
    return 0.0 if rank is None else 1.0 / rank


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-relevance nDCG; the ideal ordering puts every relevant item first."""

    if not relevant:
        return 0.0
    dcg = 0.0
    for index, item in enumerate(ranked[:k], start=1):
        if item in relevant:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def aggregate(
    rankings: Sequence[tuple[Sequence[str], set[str]]],
    *,
    k: int = 5,
) -> dict[str, float]:
    """Mean recall@k, MRR@k and nDCG@k across questions with relevant ids."""

    rows = [(ranked, relevant) for ranked, relevant in rankings if relevant]
    if not rows:
        return {"questions": 0, f"recall@{k}": 0.0, f"mrr@{k}": 0.0, f"ndcg@{k}": 0.0}
    count = len(rows)
    return {
        "questions": count,
        f"recall@{k}": round(sum(recall_at_k(r, rel, k) for r, rel in rows) / count, 4),
        f"mrr@{k}": round(sum(reciprocal_rank(r, rel, k) for r, rel in rows) / count, 4),
        f"ndcg@{k}": round(sum(ndcg_at_k(r, rel, k) for r, rel in rows) / count, 4),
    }


__all__ = [
    "aggregate",
    "first_relevant_rank",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
]
