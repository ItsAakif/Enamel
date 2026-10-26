"""Provider embeddings with a dimension guard (T125).

Embeddings always come from the configured provider (``providers.get_provider``),
so the mock provider is used offline and OpenRouter in production. Before a
vector is handed to the database we assert it matches ``AI_EMBEDDING_DIM``; a
mismatch is a configuration error, not a silent truncation, because the pgvector
column is fixed at migration time.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.conf import settings

from ..providers.base import AIProvider
from ..providers.embeddings import get_embedding_provider


class EmbeddingDimensionError(ValueError):
    """Raised when a provider returns a vector of the wrong dimension."""

    code = "embedding_dimension_mismatch"

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            f"Embedding dimension mismatch: expected {expected}, provider returned {actual}."
        )
        self.expected = expected
        self.actual = actual


def expected_dimension() -> int:
    return int(getattr(settings, "AI_EMBEDDING_DIM", 8) or 8)


def embed_texts(
    texts: Sequence[str],
    *,
    provider: AIProvider | None = None,
    dimension: int | None = None,
) -> list[list[float]]:
    """Embed ``texts`` and validate every vector's dimension."""

    items = list(texts)
    if not items:
        return []
    active = provider or get_embedding_provider()
    vectors = [list(vector) for vector in active.embed(items)]
    if len(vectors) != len(items):
        raise EmbeddingDimensionError(len(items), len(vectors))
    expected = int(dimension or expected_dimension())
    for vector in vectors:
        if len(vector) != expected:
            raise EmbeddingDimensionError(expected, len(vector))
    return vectors


def embed_text(
    text: str,
    *,
    provider: AIProvider | None = None,
    dimension: int | None = None,
) -> list[float]:
    """Embed one string and return its vector."""

    return embed_texts([text], provider=provider, dimension=dimension)[0]


__all__ = ["EmbeddingDimensionError", "embed_text", "embed_texts", "expected_dimension"]
