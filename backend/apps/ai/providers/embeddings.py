"""Embedding providers for the knowledge base.

The chat provider and the embedding provider are separate concerns: hosted
chat APIs (including the free OpenRouter models) do not expose free embeddings,
while the local demo needs real semantic vectors. This module therefore offers:

* ``mock``      - the deterministic provider used by tests and offline runs;
* ``fastembed`` - a local ONNX sentence model (``BAAI/bge-small-en-v1.5``,
                  384 dimensions), optional dependency, no network after the
                  first model download;
* ``openrouter``- the chat provider's ``/embeddings`` endpoint, for an operator
                  that configures a paid embedding model.

``fastembed`` is imported lazily so the base install (and CI) never needs
onnxruntime. A missing optional dependency raises a clear configuration error
instead of silently falling back to the mock, because a silent fallback would
make a retrieval benchmark meaningless.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any, Protocol

from django.conf import settings

from .factory import get_provider


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingConfigurationError(RuntimeError):
    """The configured embedding provider cannot serve this process."""


class FastEmbedProvider:
    """Local ONNX embeddings via ``fastembed`` (optional dependency)."""

    _model: Any = None
    _lock = threading.Lock()

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = str(
            model_name
            or getattr(settings, "AI_EMBEDDING_MODEL", "")
            or "BAAI/bge-small-en-v1.5"
        )

    @classmethod
    def _load(cls, model_name: str) -> Any:
        if cls._model is None:
            with cls._lock:
                if cls._model is None:
                    try:
                        from fastembed import TextEmbedding
                    except ImportError as exc:  # pragma: no cover - optional dep
                        raise EmbeddingConfigurationError(
                            "AI_EMBEDDING_PROVIDER=fastembed requires the optional "
                            "'fastembed' package (pip install fastembed)."
                        ) from exc
                    cls._model = TextEmbedding(model_name=model_name)
        return cls._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text) for text in texts]
        if not items:
            return []
        model = self._load(self.model_name)
        return [[float(value) for value in vector] for vector in model.embed(items)]


class ChatProviderEmbeddings:
    """Adapter for a chat provider that also implements ``embed``."""

    def __init__(self, name: str) -> None:
        self.provider = get_provider(name)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(vector) for vector in self.provider.embed(list(texts))]


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Resolve the configured embedding provider.

    ``mock`` and ``openrouter`` reuse the chat provider (both implement
    ``embed``); ``fastembed`` is the local ONNX model.
    """

    active = str(name or getattr(settings, "AI_EMBEDDING_PROVIDER", "mock") or "mock").lower()
    if active == "fastembed":
        return FastEmbedProvider()
    if active in {"mock", "openrouter"}:
        return ChatProviderEmbeddings(active)
    raise EmbeddingConfigurationError(
        f"Unknown AI_EMBEDDING_PROVIDER '{active}'; expected mock, fastembed or openrouter."
    )


__all__ = [
    "ChatProviderEmbeddings",
    "EmbeddingConfigurationError",
    "EmbeddingProvider",
    "FastEmbedProvider",
    "get_embedding_provider",
]
