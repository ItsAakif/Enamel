"""Idempotent knowledge ingestion (T125).

Ingestion is keyed on ``(clinic, sha256)`` of the *normalized* text. The digest
is stored on the document, so re-ingesting identical content returns the existing
document and its existing chunk ids unchanged -- no duplicate chunks, no new
embeddings. Archiving flips the document and its chunks to ``archived`` so
retrieval stops returning them immediately; re-ingesting archived content
reactivates the same rows rather than creating new ones.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction

from ..providers.base import AIProvider
from .chunk import chunk_text, normalize_text
from .embed import embed_texts

TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".text")
PDF_SUFFIXES = (".pdf",)


class UnsupportedFileError(ValueError):
    """Raised for a file type ingestion cannot read without a new dependency."""

    code = "unsupported_file"


@dataclass
class IngestResult:
    document: Any
    chunk_ids: list[str]
    created: bool
    chunk_count: int
    reactivated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kind_choices() -> tuple[str, ...]:
    from ..models import KnowledgeDocument

    return tuple(KnowledgeDocument.Kind.values)


def _resolve_kind(kind: Any) -> str:
    from ..models import KnowledgeDocument

    value = str(kind or KnowledgeDocument.Kind.POLICY).strip().lower()
    if value not in _kind_choices():
        raise ValueError(f"Unknown knowledge kind '{value}'.")
    return value


def _chunk_ids(document: Any) -> list[str]:
    from ..models import KnowledgeChunk

    rows = (
        KnowledgeChunk.all_objects.filter(document=document)
        .order_by("ordinal", "id")
        .values_list("id", flat=True)
    )
    return [str(pk) for pk in rows]


def _reactivate(document: Any) -> None:
    from ..models import KnowledgeChunk, KnowledgeDocument

    document.status = KnowledgeDocument.Status.ACTIVE
    document.save(update_fields=["status", "updated_at"])
    KnowledgeChunk.all_objects.filter(document=document).update(
        status=KnowledgeChunk.Status.ACTIVE
    )


def ingest_document(
    clinic: Any,
    *,
    title: str,
    text: str,
    source: str = "",
    kind: str = "policy",
    created_by: Any = None,
    provider: AIProvider | None = None,
    max_tokens: int | None = None,
    overlap: int | None = None,
) -> IngestResult:
    """Ingest ``text`` for ``clinic``; idempotent by ``(clinic, sha256)``.

    The document and its chunks are written inside the clinic's scope: the
    rows belong to the clinic, and the row-level policies address them by it
    (ADR-013).
    """

    from apps.core.tenancy import with_clinic

    with with_clinic(clinic):
        return _ingest_document(
            clinic,
            title=title,
            text=text,
            source=source,
            kind=kind,
            created_by=created_by,
            provider=provider,
            max_tokens=max_tokens,
            overlap=overlap,
        )


def _ingest_document(
    clinic: Any,
    *,
    title: str,
    text: str,
    source: str = "",
    kind: str = "policy",
    created_by: Any = None,
    provider: AIProvider | None = None,
    max_tokens: int | None = None,
    overlap: int | None = None,
) -> IngestResult:
    from ..models import KnowledgeChunk, KnowledgeDocument

    normalized = normalize_text(text)
    digest = _sha256_text(normalized)
    resolved_kind = _resolve_kind(kind)

    existing = KnowledgeDocument.all_objects.filter(clinic=clinic, sha256=digest).first()
    if existing is not None:
        reactivated = existing.status != KnowledgeDocument.Status.ACTIVE
        if reactivated:
            _reactivate(existing)
        return IngestResult(
            document=existing,
            chunk_ids=_chunk_ids(existing),
            created=False,
            chunk_count=existing.chunk_count,
            reactivated=reactivated,
        )

    max_tokens = int(max_tokens or getattr(settings, "RAG_CHUNK_TOKENS", 800) or 800)
    overlap = int(overlap if overlap is not None else getattr(settings, "RAG_CHUNK_OVERLAP", 120))
    chunks = chunk_text(normalized, max_tokens=max_tokens, overlap=overlap)

    # Embed before any write: a dimension mismatch or provider failure must not
    # leave an empty document row behind.
    vectors = (
        embed_texts([chunk.text for chunk in chunks], provider=provider) if chunks else []
    )

    with transaction.atomic():
        document = KnowledgeDocument.all_objects.create(
            clinic=clinic,
            title=str(title or source or "Untitled document")[:255],
            source=str(source or "")[:255],
            kind=resolved_kind,
            sha256=digest,
            size_bytes=len(normalized.encode("utf-8")),
            status=KnowledgeDocument.Status.ACTIVE,
            chunk_count=len(chunks),
            created_by=created_by,
        )

        if chunks:
            rows = [
                KnowledgeChunk(
                    clinic=clinic,
                    document=document,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    metadata_json=chunk.metadata,
                    embedding=vector,
                    status=KnowledgeChunk.Status.ACTIVE,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            KnowledgeChunk.all_objects.bulk_create(rows)

    return IngestResult(
        document=document,
        chunk_ids=_chunk_ids(document),
        created=True,
        chunk_count=len(chunks),
    )


def ingest_file(
    clinic: Any,
    *,
    title: str,
    filename: str,
    content: bytes | str,
    source: str = "",
    kind: str = "policy",
    created_by: Any = None,
    provider: AIProvider | None = None,
) -> IngestResult:
    """Ingest a text/markdown file. PDFs need an extractor that is not bundled."""

    name = str(filename or "").lower()
    suffix = Path(name).suffix

    if suffix in PDF_SUFFIXES:
        raise UnsupportedFileError(
            "PDF extraction is not available in this build; upload text or markdown."
        )
    if suffix and suffix not in TEXT_SUFFIXES:
        raise UnsupportedFileError(
            f"Unsupported file type '{suffix}'; upload text or markdown."
        )

    if isinstance(content, bytes):
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedFileError("The file is not valid UTF-8 text.") from exc
    else:
        decoded = str(content)

    return ingest_document(
        clinic,
        title=title or Path(name).stem or "Untitled document",
        text=decoded,
        source=source or filename,
        kind=kind,
        created_by=created_by,
        provider=provider,
    )


def archive_document(document: Any) -> Any:
    """Archive a document and its chunks so retrieval stops returning them."""

    from apps.core.tenancy import with_clinic

    from ..models import KnowledgeChunk, KnowledgeDocument

    with with_clinic(document.clinic_id):
        document.status = KnowledgeDocument.Status.ARCHIVED
        document.save(update_fields=["status", "updated_at"])
        KnowledgeChunk.all_objects.filter(document=document).update(
            status=KnowledgeChunk.Status.ARCHIVED
        )
    return document


__all__ = [
    "TEXT_SUFFIXES",
    "IngestResult",
    "UnsupportedFileError",
    "archive_document",
    "ingest_document",
    "ingest_file",
]
