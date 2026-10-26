"""Code-owned prompt registry (T123).

The registry is the single place that maps a prompt key to its version, purpose,
body and JSON output schema. Bodies live in code (``assistant.py``,
``summary.py``); the database stores only metadata via
:func:`sync_prompt_templates`.

``get_prompt`` is the lookup used by the invocation pipeline; it raises
:class:`PromptNotFound` for an unknown key so the caller can degrade safely
instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .assistant import (
    ASSISTANT_CHAT_KEY,
    ASSISTANT_CHAT_SYSTEM,
    ASSISTANT_CHAT_VERSION,
    INTENT_KEY,
    INTENT_SYSTEM,
    INTENT_VERSION,
    KNOWLEDGE_ANSWER_KEY,
    KNOWLEDGE_ANSWER_SYSTEM,
    KNOWLEDGE_ANSWER_VERSION,
    IntentResult,
)
from .summary import SUMMARY_KEY, SUMMARY_SYSTEM, SUMMARY_VERSION, VoiceSummary


class PromptNotFound(KeyError):
    """Raised when a prompt key is not registered."""


@dataclass(frozen=True)
class PromptSpec:
    key: str
    version: int
    purpose: str
    body: str
    model: type[BaseModel] | None = None
    schema_json: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True

    def as_metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "version": self.version,
            "purpose": self.purpose,
            "schema_json": self.schema_json,
            "is_active": self.is_active,
        }


_REGISTRY: dict[str, PromptSpec] = {}


def register(spec: PromptSpec) -> PromptSpec:
    _REGISTRY[spec.key] = spec
    return spec


def _schema_for(model: type[BaseModel] | None) -> dict[str, Any]:
    if model is None:
        return {}
    return model.model_json_schema()


def _register_defaults() -> None:
    register(
        PromptSpec(
            key=ASSISTANT_CHAT_KEY,
            version=ASSISTANT_CHAT_VERSION,
            purpose="chat",
            body=ASSISTANT_CHAT_SYSTEM,
            model=None,
            schema_json={},
        )
    )
    register(
        PromptSpec(
            key=KNOWLEDGE_ANSWER_KEY,
            version=KNOWLEDGE_ANSWER_VERSION,
            purpose="rag_answer",
            body=KNOWLEDGE_ANSWER_SYSTEM,
            model=None,
            schema_json={},
        )
    )
    register(
        PromptSpec(
            key=INTENT_KEY,
            version=INTENT_VERSION,
            purpose="voice_intent",
            body=INTENT_SYSTEM,
            model=IntentResult,
            schema_json=_schema_for(IntentResult),
        )
    )
    register(
        PromptSpec(
            key=SUMMARY_KEY,
            version=SUMMARY_VERSION,
            purpose="voice_summary",
            body=SUMMARY_SYSTEM,
            model=VoiceSummary,
            schema_json=_schema_for(VoiceSummary),
        )
    )


def get_prompt(key: str, version: int | None = None) -> PromptSpec:
    spec = _REGISTRY.get(key)
    if spec is None:
        raise PromptNotFound(key)
    if version is not None and spec.version != int(version):
        raise PromptNotFound(f"{key}@{version}")
    return spec


def all_prompts() -> list[PromptSpec]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def prompt_metadata() -> list[dict[str, Any]]:
    return [spec.as_metadata() for spec in all_prompts()]


def sync_prompt_templates() -> list[Any]:
    """Upsert ``PromptTemplate`` metadata rows for every registered prompt.

    Idempotent: the ``(key, version)`` row is created once and its metadata is
    refreshed in place. No prompt body is ever written to the database.
    """

    from ..models import PromptTemplate

    rows: list[PromptTemplate] = []
    for spec in all_prompts():
        row, _created = PromptTemplate.objects.update_or_create(
            key=spec.key,
            version=spec.version,
            defaults={
                "purpose": spec.purpose,
                "schema_json": spec.schema_json,
                "is_active": spec.is_active,
            },
        )
        rows.append(row)
    return rows


_register_defaults()


__all__ = [
    "PromptNotFound",
    "PromptSpec",
    "all_prompts",
    "get_prompt",
    "prompt_metadata",
    "register",
    "sync_prompt_templates",
]
