"""``lookup_faq`` tool (T124).

A tiny code-owned FAQ table. The RAG knowledge base (T125) replaces this lookup;
until then the tool answers only from fixed text, so it can never fabricate
clinical advice or leak another patient's data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .base import Tool, ToolContext

FAQ_ENTRIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hour", "open", "close", "timing"), "Our clinic is open Monday to Saturday, 9am to 6pm."),
    (("park", "parking"), "There is parking behind the building; the entrance is on the side street."),
    (("address", "where", "location"), "Ask the reception team for the exact address and directions."),
    (("insurance", "cover", "payment"), "We accept most major insurers; the front desk can confirm your plan."),
    (("emergency", "urgent", "pain"), "For a dental emergency, please call the clinic directly right away."),
    (("cancel", "reschedule", "change"), "I can help cancel or move an appointment once you confirm your details."),
    (("cost", "price", "fee"), "Fees depend on the treatment; reception can give you an exact quote."),
)


class FaqArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


def _handler(ctx: ToolContext, args: FaqArgs) -> dict[str, Any]:
    query = (args.query or "").lower()
    for keywords, answer in FAQ_ENTRIES:
        if any(keyword in query for keyword in keywords):
            return {"answer": answer, "matched": True, "topic": keywords[0]}
    return {
        "answer": "I do not have that information. I can pass your question to the clinic team.",
        "matched": False,
        "topic": "",
    }


TOOL = Tool(
    name="lookup_faq",
    description="Answer a general clinic question from the fixed FAQ.",
    args_model=FaqArgs,
    handler=_handler,
    required_perms=("ai.use",),
    consequential=False,
    requires_patient=False,
)


__all__ = ["FAQ_ENTRIES", "TOOL", "FaqArgs"]
