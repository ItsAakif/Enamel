"""ASR adapter protocol and the local simulator (T124)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

DEFAULT_CONFIDENCE = 0.95


@runtime_checkable
class AsrAdapter(Protocol):
    name: str

    def transcribe(self, call: Any, audio: Any, *, text: str = "", confidence: float = DEFAULT_CONFIDENCE) -> tuple[str, float]: ...


class LocalTestAsr:
    """Text-only ASR simulator: the 'audio' is already text."""

    name = "local_test"
    label = "Simulator"

    def transcribe(
        self,
        call: Any,
        audio: Any,
        *,
        text: str = "",
        confidence: float = DEFAULT_CONFIDENCE,
    ) -> tuple[str, float]:
        return str(text or audio or ""), float(confidence)


__all__ = ["DEFAULT_CONFIDENCE", "AsrAdapter", "LocalTestAsr"]
