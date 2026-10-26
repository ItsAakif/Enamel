"""Deterministic, paragraph-aware chunking (T125).

Token model
-----------
There is no tokenizer dependency in the backend, so the *approximate* token
count is the number of whitespace-delimited words in a piece of text. This is
documented, deterministic and good enough for a chunk-size target: real
tokenizers split a little finer, but the ordering, ordinals and metadata we
persist never depend on the exact count.

Algorithm
---------
1. Split the normalized text on blank lines into paragraphs.
2. A paragraph longer than ``max_tokens`` is split on whitespace into pieces of
   at most ``max_tokens`` words so no unit is ever oversized.
3. Greedily pack consecutive units into a chunk until the next unit would push
   it past ``max_tokens``; the new chunk then starts with the trailing
   ``overlap`` words of the previous chunk (a word-level overlap).

The result is stable: the same text, ``max_tokens`` and ``overlap`` always
produce the same chunks in the same order with the same metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def normalize_text(text: str) -> str:
    """Collapse trailing whitespace per line and trim the whole document."""

    lines = [line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip()


def count_tokens(text: str) -> int:
    """Approximate token count: the number of whitespace-delimited words."""

    return len(str(text or "").split())


def _paragraphs(text: str) -> list[str]:
    parts = PARAGRAPH_SPLIT_RE.split(text)
    return [part.strip() for part in parts if part.strip()]


def _split_oversized(paragraph: str, max_tokens: int) -> list[str]:
    words = paragraph.split()
    if len(words) <= max_tokens:
        return [paragraph]
    return [
        " ".join(words[index : index + max_tokens])
        for index in range(0, len(words), max_tokens)
    ]


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    token_count: int
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "text": self.text,
            "token_count": self.token_count,
            "metadata": dict(self.metadata),
        }


def chunk_text(
    text: str,
    *,
    max_tokens: int = 800,
    overlap: int = 120,
) -> list[Chunk]:
    """Split ``text`` into paragraph-aware chunks of at most ``max_tokens`` words."""

    normalized = normalize_text(text)
    if not normalized:
        return []

    max_tokens = max(1, int(max_tokens))
    overlap = max(0, min(int(overlap), max_tokens - 1))

    units: list[str] = []
    for paragraph in _paragraphs(normalized):
        units.extend(_split_oversized(paragraph, max_tokens))

    chunks: list[Chunk] = []
    current_words: list[str] = []
    for unit in units:
        unit_words = unit.split()
        if current_words and len(current_words) + len(unit_words) > max_tokens:
            text_out = " ".join(current_words)
            chunks.append(
                Chunk(
                    ordinal=len(chunks),
                    text=text_out,
                    token_count=len(current_words),
                    metadata={
                        "tokens": len(current_words),
                        "chars": len(text_out),
                        "overlap": overlap,
                    },
                )
            )
            current_words = current_words[-overlap:] if overlap else []
        current_words.extend(unit_words)

    if current_words:
        text_out = " ".join(current_words)
        chunks.append(
            Chunk(
                ordinal=len(chunks),
                text=text_out,
                token_count=len(current_words),
                metadata={
                    "tokens": len(current_words),
                    "chars": len(text_out),
                    "overlap": overlap,
                },
            )
        )

    return chunks


__all__ = ["Chunk", "chunk_text", "count_tokens", "normalize_text"]
