"""HNSW vector index and full-text GIN index for the knowledge base.

The vector column is queried by cosine distance; HNSW turns that from a
sequential scan into an approximate nearest-neighbour search. The lexical
branch of hybrid retrieval uses ``to_tsvector('english', text)``, so the same
expression is indexed with GIN. Both indexes are additive and reversible.
"""

from __future__ import annotations

from django.db import migrations

FORWARD = [
    (
        "CREATE INDEX IF NOT EXISTS ai_kchunk_embedding_hnsw "
        "ON ai_knowledgechunk USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ai_kchunk_text_gin "
        "ON ai_knowledgechunk USING gin (to_tsvector('english', text))"
    ),
]

REVERSE = [
    "DROP INDEX IF EXISTS ai_kchunk_text_gin",
    "DROP INDEX IF EXISTS ai_kchunk_embedding_hnsw",
]


def apply(apps, schema_editor) -> None:
    with schema_editor.connection.cursor() as cursor:
        for statement in FORWARD:
            cursor.execute(statement)


def revert(apps, schema_editor) -> None:
    with schema_editor.connection.cursor() as cursor:
        for statement in REVERSE:
            cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0003_knowledgedocument_knowledgechunk_retrievallog_and_more"),
    ]

    operations = [
        migrations.RunPython(apply, revert),
    ]
