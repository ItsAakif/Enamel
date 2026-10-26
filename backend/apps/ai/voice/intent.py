"""Rule-based intent classification and injection detection (T124).

Safety-critical decisions are deterministic and rule-based, not delegated to the
model: a caller message is classified into a small allowlist of intents, and a
prompt-injection corpus is recognised before any intent handling. The model is
still free to *propose* an action, but the registry's gates decide what runs.
"""

from __future__ import annotations

import re

INTENTS = (
    "greeting",
    "book",
    "reschedule",
    "cancel",
    "faq",
    "confirm",
    "identity",
    "escalate",
    "unknown",
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above|earlier)?\s*(instructions|prompts?|rules)", re.I),
    re.compile(r"ignore\s+.*\b(instructions|rules)\b", re.I),
    re.compile(r"disregard\s+.*\b(instructions|rules|prompt)\b", re.I),
    re.compile(r"\b(list|show|reveal|give\s+me|print)\b.*\b(all|every|other|another)\s+patients?\b", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\bdeveloper\s+mode\b", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"\boverride\s+(your\s+)?(rules|instructions|permissions)\b", re.I),
    re.compile(r"\bact\s+as\s+(an?\s+)?(admin|administrator|developer|system|root)\b", re.I),
)

_IDENTITY_HINTS = ("date of birth", "my dob", "born", "my phone", "phone number is")
_GREETING_RE = re.compile(r"\b(hello|hi|hey|good\s+(morning|afternoon|evening))\b", re.I)
_ESCALATE_WORDS = ("human", "staff", "receptionist", "talk to someone", "speak to someone", "manager")
_CONFIRM_RE = re.compile(r"\b(yes|confirm|correct|yep|sure|ok|okay|go ahead)\b", re.I)
_BOOK_WORDS = ("book", "appointment", "schedule me", "make an appointment", "availability", "slot")
_RESCHEDULE_WORDS = ("reschedule", "move my appointment", "change my appointment", "move it")
_CANCEL_WORDS = ("cancel", "call it off")
_FAQ_WORDS = ("hour", "open", "closing", "parking", "park", "address", "where are you", "cost", "price", "fee", "insurance")


def is_injection(text: str) -> bool:
    value = text or ""
    return any(pattern.search(value) for pattern in _INJECTION_PATTERNS)


def has_identity_hint(text: str) -> bool:
    value = (text or "").lower()
    if re.search(r"\d{4}-\d{2}-\d{2}", value):
        return True
    return any(hint in value for hint in _IDENTITY_HINTS)


def classify(text: str) -> str:
    """Return one of :data:`INTENTS` for ``text`` (injection is ``unknown``)."""

    value = (text or "").strip().lower()
    if not value:
        return "unknown"
    if is_injection(value):
        return "unknown"
    if any(word in value for word in _RESCHEDULE_WORDS):
        return "reschedule"
    if any(word in value for word in _CANCEL_WORDS):
        return "cancel"
    if has_identity_hint(value):
        return "identity"
    if any(word in value for word in _ESCALATE_WORDS):
        return "escalate"
    if any(word in value for word in _BOOK_WORDS):
        return "book"
    if any(word in value for word in _FAQ_WORDS):
        return "faq"
    if _CONFIRM_RE.search(value):
        return "confirm"
    if _GREETING_RE.search(value):
        return "greeting"
    return "unknown"


__all__ = ["INTENTS", "classify", "has_identity_hint", "is_injection"]
