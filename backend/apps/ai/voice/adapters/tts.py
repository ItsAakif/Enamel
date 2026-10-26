"""TTS adapter protocol and the local simulator (T124)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TtsAdapter(Protocol):
    name: str

    def synthesize(self, text: str) -> None: ...


class LocalTestTts:
    """Text-only TTS simulator: synthesis is a no-op that returns nothing."""

    name = "local_test"
    label = "Simulator"

    def synthesize(self, text: str) -> None:
        return None


__all__ = ["LocalTestTts", "TtsAdapter"]
