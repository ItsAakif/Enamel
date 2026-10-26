"""Knowledge ingestion tests (T125): chunking, idempotency, archive, dimensions."""

from __future__ import annotations

import pytest

from apps.ai.models import KnowledgeChunk, KnowledgeDocument
from apps.ai.providers.mock import MockProvider
from apps.ai.rag.chunk import chunk_text
from apps.ai.rag.embed import EmbeddingDimensionError
from apps.ai.rag.ingest import (
    UnsupportedFileError,
    archive_document,
    ingest_document,
    ingest_file,
)

LONG_TEXT = (
    ("Paragraph one about opening hours and appointments at the clinic. " * 60)
    + "\n\n"
    + ("Paragraph two about insurance, payment plans and estimates. " * 60)
)


def test_chunk_ordinals_and_metadata_are_stable():
    first = chunk_text(LONG_TEXT, max_tokens=100, overlap=20)
    second = chunk_text(LONG_TEXT, max_tokens=100, overlap=20)

    assert len(first) > 1
    assert [chunk.ordinal for chunk in first] == list(range(len(first)))
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert [chunk.metadata for chunk in first] == [chunk.metadata for chunk in second]
    for chunk in first:
        assert set(chunk.metadata) == {"tokens", "chars", "overlap"}
        assert chunk.token_count == chunk.metadata["tokens"]


def test_ingest_creates_stable_ordinals(clinic_factory):
    clinic = clinic_factory()
    result = ingest_document(clinic, title="Handbook", text=LONG_TEXT)

    assert result.created is True
    assert result.chunk_count > 1
    chunks = list(
        KnowledgeChunk.all_objects.filter(document=result.document).order_by("ordinal")
    )
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_count > 0 for chunk in chunks)
    assert all(len(chunk.embedding) == 384 for chunk in chunks)


def test_reingest_is_a_noop_with_identical_chunk_ids(clinic_factory):
    clinic = clinic_factory()
    first = ingest_document(clinic, title="Handbook", text=LONG_TEXT)
    second = ingest_document(clinic, title="Handbook", text=LONG_TEXT)

    assert first.created is True
    assert second.created is False
    assert second.chunk_ids == first.chunk_ids
    assert KnowledgeDocument.all_objects.filter(clinic=clinic).count() == 1
    assert (
        KnowledgeChunk.all_objects.filter(document=first.document).count()
        == first.chunk_count
    )


def test_embedding_dimension_mismatch_fails_cleanly(clinic_factory):
    clinic = clinic_factory()
    with pytest.raises(EmbeddingDimensionError):
        ingest_document(
            clinic,
            title="Bad",
            text="Some clinic policy text.",
            provider=MockProvider(embedding_dim=4),
        )
    # No partial write: the document was never created.
    assert KnowledgeDocument.all_objects.filter(clinic=clinic).count() == 0


def test_ingest_file_accepts_text_and_rejects_unsupported(clinic_factory):
    clinic = clinic_factory()
    result = ingest_file(
        clinic,
        title="Policy",
        filename="policy.md",
        content=b"# Policy\n\nOur clinic is open on weekdays.",
    )
    assert result.created is True
    assert result.chunk_count >= 1

    with pytest.raises(UnsupportedFileError):
        ingest_file(clinic, title="Scan", filename="scan.pdf", content=b"%PDF-1.4")
    with pytest.raises(UnsupportedFileError):
        ingest_file(clinic, title="Bin", filename="tool.exe", content=b"MZ")


def test_archive_removes_document_and_chunks_from_active(clinic_factory):
    clinic = clinic_factory()
    result = ingest_document(clinic, title="Handbook", text=LONG_TEXT)
    archive_document(result.document)

    result.document.refresh_from_db()
    assert result.document.status == KnowledgeDocument.Status.ARCHIVED
    assert (
        KnowledgeChunk.all_objects.filter(
            document=result.document, status=KnowledgeChunk.Status.ACTIVE
        ).count()
        == 0
    )


def test_reingest_reactivates_archived_document(clinic_factory):
    clinic = clinic_factory()
    first = ingest_document(clinic, title="Handbook", text=LONG_TEXT)
    archive_document(first.document)

    again = ingest_document(clinic, title="Handbook", text=LONG_TEXT)
    assert again.created is False
    assert again.reactivated is True
    assert again.chunk_ids == first.chunk_ids
    again.document.refresh_from_db()
    assert again.document.status == KnowledgeDocument.Status.ACTIVE
