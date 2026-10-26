"""Telephony adapter protocol and the local simulator (T124).

Live PSTN telephony is out of scope. The only implementation is
:class:`LocalTestTelephony`, which is text-only, never opens a socket, and is
surfaced in the console as ``Simulator``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TelephonyAdapter(Protocol):
    name: str

    def start(self, call: Any) -> None: ...

    def stop(self, call: Any) -> None: ...

    def transfer(self, call: Any, target: str = "staff") -> None: ...

    def record(self, call: Any, consent: bool) -> str | None: ...


class LocalTestTelephony:
    """Text-only telephony simulator. Records a key only with consent."""

    name = "local_test"
    label = "Simulator"

    def start(self, call: Any) -> None:
        return None

    def stop(self, call: Any) -> None:
        return None

    def transfer(self, call: Any, target: str = "staff") -> None:
        return None

    def record(self, call: Any, consent: bool) -> str | None:
        # No audio is ever stored by the simulator; a key is minted only when the
        # caller consented, so the call record can prove the consent decision.
        if not consent:
            return None
        return f"local_test/{getattr(call, 'id', 'call')}"


__all__ = ["LocalTestTelephony", "TelephonyAdapter"]
