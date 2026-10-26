"""Conversation state machine (T124).

``greeting -> intent -> identity -> action -> confirmation -> outcome`` with
``escalated`` and ``ended`` as terminal states. The engine consults
:func:`can_transition` before every move so an illegal jump (for example from
``greeting`` straight to ``outcome``) is a programming error, not a silent
state corruption.
"""

from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    GREETING = "greeting"
    INTENT = "intent"
    IDENTITY = "identity"
    ACTION = "action"
    CONFIRMATION = "confirmation"
    OUTCOME = "outcome"
    ESCALATED = "escalated"
    ENDED = "ended"


TRANSITIONS: dict[State, frozenset[State]] = {
    State.GREETING: frozenset({State.INTENT, State.ESCALATED, State.ENDED}),
    State.INTENT: frozenset({State.IDENTITY, State.ACTION, State.ESCALATED, State.ENDED}),
    State.IDENTITY: frozenset({State.ACTION, State.ESCALATED, State.ENDED}),
    State.ACTION: frozenset({State.CONFIRMATION, State.OUTCOME, State.ESCALATED, State.ENDED}),
    State.CONFIRMATION: frozenset({State.OUTCOME, State.ACTION, State.ESCALATED, State.ENDED}),
    State.OUTCOME: frozenset({State.INTENT, State.ENDED}),
    State.ESCALATED: frozenset({State.ENDED}),
    State.ENDED: frozenset(),
}

TERMINAL = frozenset({State.ESCALATED, State.ENDED})


def can_transition(source: State | str, target: State | str) -> bool:
    try:
        return State(str(target)) in TRANSITIONS[State(str(source))]
    except (KeyError, ValueError):
        return False


def assert_transition(source: State | str, target: State | str) -> None:
    if not can_transition(source, target):
        raise ValueError(f"Illegal conversation transition {source} -> {target}")


__all__ = ["TERMINAL", "TRANSITIONS", "State", "assert_transition", "can_transition"]
