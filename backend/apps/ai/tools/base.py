"""Tool contract (T124).

Every tool declares four things and one handler:

* a pydantic ``args_model`` (``extra="forbid"`` so a hallucinated field is
  rejected before any domain code runs);
* ``required_perms`` -- re-checked at execution time, not just at proposal time;
* ``consequential`` -- whether the tool changes state and therefore needs an
  explicit caller confirmation;
* ``requires_patient`` -- whether the tool needs the server-resolved patient.

The execution context (:class:`ToolContext`) is where the clinic and the
permitted patient live. The model can never supply ``clinic_id`` or
``patient_id``: those are not fields of any args model, and the context is built
from the request scope and the call record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel


class ToolError(Exception):
    """Base class for tool-layer failures."""

    code = "tool_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field_errors: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.details = details or {}


class ToolRejected(ToolError):
    """A gate refused the call (unknown tool, bad args, confirmation, ...)."""

    code = "tool_rejected"


@dataclass
class ToolContext:
    """Everything a tool may act on. Built by the caller, never by the model."""

    clinic: Any
    user: Any = None
    membership: Any = None
    call: Any = None
    patient: Any = None
    request: Any = None
    confirmed: bool = False
    confirmation_nonce: str = ""
    actor: Any = None

    def with_confirmation(self, *, confirmed: bool, nonce: str) -> ToolContext:
        return replace(self, confirmed=confirmed, confirmation_nonce=nonce)


@dataclass
class ToolResult:
    tool: str
    status: str = "ok"  # ok | rejected | error
    result: dict[str, Any] | None = None
    error_code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


Handler = Callable[[ToolContext, BaseModel], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    required_perms: tuple[str, ...] = ()
    consequential: bool = False
    requires_patient: bool = False

    def execute(self, ctx: ToolContext, args: BaseModel) -> dict[str, Any]:
        return self.handler(ctx, args)

    def arg_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arg_schema(),
            },
        }


__all__ = [
    "Handler",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRejected",
    "ToolResult",
]
