"""Structured-output validation and repair (T123)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from apps.ai.providers.mock import MockProvider
from apps.ai.services.structured import (
    AIOutputInvalid,
    complete_structured,
    extract_json,
    validate_text,
)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    confidence: float


def _messages():
    return [{"role": "user", "content": "answer in json"}]


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_invalid_raises():
    with pytest.raises(AIOutputInvalid):
        extract_json("not json")


def test_validate_text_rejects_non_object():
    with pytest.raises(AIOutputInvalid):
        validate_text("[1, 2, 3]", Answer)


def test_validate_text_rejects_schema_mismatch():
    with pytest.raises(AIOutputInvalid):
        validate_text(json.dumps({"answer": "x"}), Answer)


def test_complete_structured_valid_first_try():
    provider = MockProvider()
    model, response = complete_structured(provider, _messages(), Answer)
    assert isinstance(model, Answer)
    assert model.answer


def test_complete_structured_repairs_once_then_succeeds():
    provider = MockProvider(faults=["invalid_json"])
    model, response = complete_structured(provider, _messages(), Answer)
    assert isinstance(model, Answer)
    # Two provider calls: the invalid reply plus the repair.
    assert provider.health()["faults"] == 0


def test_complete_structured_persistent_invalid_raises():
    provider = MockProvider(faults=["invalid_json", "invalid_json"])
    with pytest.raises(AIOutputInvalid):
        complete_structured(provider, _messages(), Answer)


def test_complete_structured_repair_is_bounded_to_one():
    provider = MockProvider(faults=["invalid_json", "invalid_json", "invalid_json"])
    with pytest.raises(AIOutputInvalid):
        complete_structured(provider, _messages(), Answer)
    # Only the first two faults were consumed: the repair attempt is one.
    assert provider.health()["faults"] == 1
