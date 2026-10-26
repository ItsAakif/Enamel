"""Candidate reranking for retrieval.

``fastembed`` provides a local ONNX cross-encoder (no API, deterministic on
CPU). When the optional dependency or the model is unavailable, a deterministic
term-overlap reranker is used instead; it is weaker but honest, and the
retrieval report records which one ran. ``off`` disables reranking entirely.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from django.conf import settings

from .retrieve import RetrievedChunk

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i in is it of on or "
    "that the this to was what when where which who why will with you your".split()
)


class _CrossEncoderHolder:
    model: Any = None
    model_name: str = ""
    lock = threading.Lock()


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(str(text or "").lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def lexical_rerank(query: str, chunks: Sequence[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Deterministic fallback: query-term overlap, then the original score."""

    query_terms = _tokens(query)
    if not query_terms:
        return list(chunks)[:top_k]
    scored: list[tuple[float, float, RetrievedChunk]] = []
    for chunk in chunks:
        chunk_terms = _tokens(chunk.text)
        overlap = len(query_terms & chunk_terms) / len(query_terms)
        scored.append((overlap, float(chunk.score), chunk))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2].ordinal))
    return [replace(chunk, score=round(overlap, 6)) for overlap, _score, chunk in scored[:top_k]]


def _fastembed_rerank(model_name: str, query: str, chunks: Sequence[RetrievedChunk]) -> list[float] | None:
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError:
        return None
    if _CrossEncoderHolder.model is None or _CrossEncoderHolder.model_name != model_name:
        with _CrossEncoderHolder.lock:
            if _CrossEncoderHolder.model is None or _CrossEncoderHolder.model_name != model_name:
                _CrossEncoderHolder.model = TextCrossEncoder(model_name=model_name)
                _CrossEncoderHolder.model_name = model_name
    scores = list(_CrossEncoderHolder.model.rerank(query, [chunk.text for chunk in chunks]))
    return [float(score) for score in scores]


def rerank_chunks(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    top_k: int,
    mode: str | None = None,
) -> list[RetrievedChunk]:
    """Rerank ``chunks`` and return at most ``top_k``.

    ``mode`` defaults to ``settings.RAG_RERANKER``. Any fastembed failure
    (missing package, model download failure) degrades to the lexical reranker
    rather than failing the request; the caller can record which mode ran by
    comparing the scores it receives.
    """

    active = str(mode or getattr(settings, "RAG_RERANKER", "fastembed") or "fastembed").lower()
    limited = list(chunks)[: max(1, int(getattr(settings, "RAG_RERANK_TOP_N", 20) or 20))]
    if active in {"off", "none", ""} or not limited:
        return list(limited)[:top_k]
    if active == "lexical":
        return lexical_rerank(query, limited, top_k)

    model_name = str(
        getattr(settings, "AI_RERANK_MODEL", "") or "Xenova/ms-marco-MiniLM-L-6-v2"
    )
    try:
        scores = _fastembed_rerank(model_name, query, limited)
    except Exception:  # noqa: BLE001 - optional model failures must degrade
        scores = None
    if scores is None or len(scores) != len(limited):
        return lexical_rerank(query, limited, top_k)

    ranked = sorted(
        (
            replace(chunk, score=round(float(score), 6))
            for chunk, score in zip(limited, scores, strict=False)
        ),
        key=lambda chunk: (-chunk.score, chunk.ordinal),
    )
    return ranked[:top_k]


__all__ = ["lexical_rerank", "rerank_chunks"]
