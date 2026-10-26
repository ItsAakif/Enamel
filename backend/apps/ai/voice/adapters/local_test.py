"""The ``local_test`` adapter bundle (T124).

A single object that names the simulator explicitly (``mode="local_test"``,
``label="Simulator"``) and bundles the three text-only adapters, so the engine
and the console agree on what is being simulated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .asr import LocalTestAsr
from .telephony import LocalTestTelephony
from .tts import LocalTestTts

MODE = "local_test"
LABEL = "Simulator"


@dataclass
class LocalTestAdapter:
    telephony: LocalTestTelephony = field(default_factory=LocalTestTelephony)
    asr: LocalTestAsr = field(default_factory=LocalTestAsr)
    tts: LocalTestTts = field(default_factory=LocalTestTts)
    mode: str = MODE
    label: str = LABEL


__all__ = ["LABEL", "MODE", "LocalTestAdapter"]
