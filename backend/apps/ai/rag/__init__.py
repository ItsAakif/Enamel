"""RAG knowledge base (T125): ingest, chunk, embed, retrieve, answer.

The package is deliberately split so each stage is independently testable:

* :mod:`chunk` -- deterministic, paragraph-aware chunking.
* :mod:`embed` -- provider embeddings with a dimension guard.
* :mod:`ingest` -- idempotent document ingestion.
* :mod:`retrieve` -- clinic-scoped cosine retrieval.
* :mod:`answer` -- grounded answer composition with refusal.

Retrieved content is *data*: it is wrapped and never interpreted as
instructions.
"""

from __future__ import annotations

from .answer import REFUSAL_TEXT, GroundedAnswer, answer_question
from .chunk import Chunk, chunk_text, count_tokens
from .embed import EmbeddingDimensionError, embed_text, embed_texts
from .ingest import (
    IngestResult,
    UnsupportedFileError,
    archive_document,
    ingest_document,
    ingest_file,
)
from .retrieve import RetrievedChunk, search, search_by_vector

__all__ = [
    "REFUSAL_TEXT",
    "Chunk",
    "EmbeddingDimensionError",
    "GroundedAnswer",
    "IngestResult",
    "RetrievedChunk",
    "UnsupportedFileError",
    "answer_question",
    "archive_document",
    "chunk_text",
    "count_tokens",
    "embed_text",
    "embed_texts",
    "ingest_document",
    "ingest_file",
    "search",
    "search_by_vector",
]
