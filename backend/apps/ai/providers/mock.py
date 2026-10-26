"""Deterministic mock provider (T123).

The mock is the only provider the test suite exercises: it never opens a socket
and every response is a pure function of the canonical request hash, so two
identical requests always produce byte-identical output.

Fault injection
---------------
A provider failure can be simulated two ways:

* ``MockProvider(faults=["429", "500", "timeout", "malformed", "empty",
  "invalid_json"])`` -- a queue consumed one entry per ``complete`` call, so the
  first call fails and the second succeeds. This is how the retry/fallback and
  repair paths are driven.
* a marker in the last user message (``[[fault:429]]``) for ad-hoc tests.

Faults ``429``/``500``/``timeout`` raise the matching transient error;
``malformed`` and ``empty`` raise :class:`ProviderMalformed` /
:class:`ProviderEmpty` (a transient HTTP-level failure); ``invalid_json``
returns a *successful* response whose content is not JSON, which exercises the
structured-output repair path rather than the transport retry path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from .base import (
    AIResponse,
    Message,
    ProviderEmpty,
    ProviderMalformed,
    ProviderRateLimited,
    ProviderServerError,
    ProviderTimeout,
    ToolCall,
    Usage,
    coerce_messages,
    request_hash,
    sha256_hex,
)

FAULT_MARKER_RE = re.compile(r"\[\[fault:([a-z0-9_]+)\]\]")
KNOWN_FAULTS = ("429", "500", "timeout", "malformed", "empty", "invalid_json")

#: A numbered context entry, as the RAG prompt renders it (``[1] source: ...``).
CONTEXT_ENTRY_RE = re.compile(r"\[(\d{1,3})\]\s+source:", re.MULTILINE)


def context_citations(messages: Sequence[Message]) -> list[int]:
    """The context entry numbers present in the prompt, in order.

    The mock is a deterministic stand-in for a model: it "uses" the material it
    was given and marks it the way the grounded-answer prompt asks for, so the
    citation contract is exercised offline exactly as it is with a real
    provider.
    """

    indexes: list[int] = []
    for message in messages:
        for match in CONTEXT_ENTRY_RE.finditer(message.content or ""):
            index = int(match.group(1))
            if index not in indexes:
                indexes.append(index)
    return indexes


def schema_value(schema: Any, seed: str, path: str = "root") -> Any:
    """Generate a deterministic value that satisfies a (simple) JSON schema.

    Supports the shapes the prompt registry declares: objects with required
    properties, arrays, enums, and the scalar types. Unknown types degrade to a
    deterministic string so the caller still receives valid JSON.
    """

    if not isinstance(schema, dict):
        return None
    for combinator in ("anyOf", "oneOf", "allOf"):
        options = schema.get(combinator)
        if isinstance(options, list) and options:
            return schema_value(options[0], seed, path)
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]

    declared = schema.get("type")
    if isinstance(declared, list):
        declared = next((t for t in declared if t != "null"), declared[0])

    if declared == "object" or "properties" in schema:
        properties = schema.get("properties") or {}
        required = schema.get("required")
        names = required if isinstance(required, list) else list(properties)
        return {
            name: schema_value(properties.get(name) or {}, seed, f"{path}.{name}")
            for name in names
            if name in properties
        }
    if declared == "array":
        items = schema.get("items") or {}
        return [schema_value(items, seed, f"{path}[0]")]
    if declared == "integer":
        return int(seed[:4], 16) % 100
    if declared == "number":
        return round((int(seed[:4], 16) % 1000) / 100.0, 2)
    if declared == "boolean":
        return bool(int(seed[:2], 16) % 2)
    if declared == "null":
        return None
    return f"value-{seed[:8]}"


class MockProvider:
    """Offline, deterministic provider."""

    name = "mock"

    def __init__(
        self,
        *,
        model: str = "mock-deterministic",
        faults: Sequence[str] | None = None,
        embedding_dim: int = 8,
    ) -> None:
        self.model = model or "mock-deterministic"
        self._faults = [str(item) for item in (faults or [])]
        self.embedding_dim = max(1, int(embedding_dim))

    # -- helpers ----------------------------------------------------------- #

    def _next_fault(self, messages: Sequence[Message]) -> str | None:
        if self._faults:
            return self._faults.pop(0)
        if messages:
            match = FAULT_MARKER_RE.search(messages[-1].content or "")
            if match:
                return match.group(1)
        return None

    def _raise_for(self, fault: str | None) -> None:
        if fault == "429":
            raise ProviderRateLimited("Simulated 429.", status_code=429)
        if fault == "500":
            raise ProviderServerError("Simulated 500.", status_code=500)
        if fault == "timeout":
            raise ProviderTimeout("Simulated timeout.")
        if fault == "malformed":
            raise ProviderMalformed("Simulated malformed body.")
        if fault == "empty":
            raise ProviderEmpty("Simulated empty choices.")

    def _tool_calls(
        self,
        messages: Sequence[Message],
        tools: Sequence[Any] | None,
        digest: str,
    ) -> list[ToolCall]:
        if not tools:
            return []
        names: set[str] = set()
        for tool in tools:
            if isinstance(tool, dict):
                function = tool.get("function") if "function" in tool else tool
                if isinstance(function, dict) and function.get("name"):
                    names.add(str(function["name"]))
        if not names or not messages:
            return []
        try:
            payload = json.loads(messages[-1].content or "")
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        spec = payload.get("tool_call")
        if not isinstance(spec, dict):
            return []
        name = str(spec.get("name") or "")
        if name not in names:
            return []
        arguments = spec.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        return [ToolCall(id=f"call_{digest[:12]}", name=name, arguments=arguments)]

    # -- AIProvider -------------------------------------------------------- #

    def complete(
        self,
        messages: Sequence[Any],
        *,
        tools: Sequence[Any] | None = None,
        response_schema: dict[str, Any] | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
    ) -> AIResponse:
        """Deterministically satisfy the protocol.

        ``response_format``, ``tool_choice``, ``temperature``, ``max_tokens``
        and ``seed`` are accepted for structural compatibility with the real
        adapter; the mock's output is a pure function of the canonical request,
        so they change nothing.
        """

        del response_format, tool_choice, temperature, max_tokens, seed, timeout
        msgs = coerce_messages(messages)
        digest = request_hash(msgs, model=self.model, tools=tools, response_schema=response_schema)
        fault = self._next_fault(msgs)
        self._raise_for(fault)

        tool_calls = self._tool_calls(msgs, tools, digest)
        if response_schema is not None:
            text = json.dumps(
                schema_value(response_schema, digest),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            text = f"Mock assistant response [{digest[:12]}]"
            # A grounded answer marks the context entries it used with [n].
            citations = context_citations(msgs)
            if citations:
                text += " " + " ".join(f"[{index}]" for index in citations)
            text += "."
        if fault == "invalid_json":
            text = "not-json:{{"

        prompt_tokens = 8 + int(digest[:4], 16) % 64
        completion_tokens = 4 + int(digest[4:8], 16) % 64
        return AIResponse(
            text=text,
            model=self.model,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            provider=self.name,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256_hex(str(text))
            vector = [
                round(int(digest[(index * 2) % 60 : (index * 2) % 60 + 2], 16) / 255.0, 6)
                for index in range(self.embedding_dim)
            ]
            vectors.append(vector)
        return vectors

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": True, "model": self.model, "faults": len(self._faults)}


__all__ = [
    "CONTEXT_ENTRY_RE",
    "KNOWN_FAULTS",
    "MockProvider",
    "context_citations",
    "schema_value",
]
