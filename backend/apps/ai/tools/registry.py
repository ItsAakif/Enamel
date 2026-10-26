"""Tool registry (T124).

The registry is the single gate every tool call passes through. It rejects, in
order, *before any domain call*:

1. an unknown tool name;
2. unknown or extra arguments (pydantic ``extra="forbid"``), which is what stops
   a model from smuggling a ``clinic_id`` or ``patient_id``;
3. a missing permission (re-checked against the live membership);
4. a missing patient identity for a patient-specific tool;
5. a missing or mismatched confirmation for a consequential tool.

Only then does it run the handler, inside one transaction, and record a
``ToolInvocation`` for every outcome. An unexpected handler failure rolls the
transaction back and raises an escalation instead of leaving a partial write.
"""

from __future__ import annotations

import json
import time
from typing import Any

from django.db import transaction
from pydantic import ValidationError

from apps.core.errors import ApiError
from apps.core.permissions import require_perm

from ..models import ToolInvocation, VoiceToolCall, VoiceTurn
from ..providers.base import canonical_json, sha256_hex
from .base import Tool, ToolContext, ToolRejected, ToolResult
from .confirmation import clear_confirmation, ensure_confirmation_request


def hash_args(raw_args: Any) -> str:
    try:
        payload = json.loads(json.dumps(raw_args or {}, sort_keys=True, default=str))
    except (TypeError, ValueError):
        payload = {"_unhashable": True}
    return sha256_hex(canonical_json(payload))


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in self.names()]

    def get(self, name: str) -> Tool:
        tool = self._tools.get(str(name or ""))
        if tool is None:
            raise ToolRejected(
                "Unknown tool.",
                code="unknown_tool",
                details={"tool": str(name or "")},
            )
        return tool

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.openai_spec() for tool in self.all()]

    # -- validation -------------------------------------------------------- #

    def validate_args(self, tool: Tool, raw_args: Any) -> Any:
        if raw_args is None:
            raw_args = {}
        if not isinstance(raw_args, dict):
            raise ToolRejected("Tool arguments must be an object.", code="invalid_arguments")
        try:
            return tool.args_model.model_validate(raw_args)
        except ValidationError as exc:
            fields = {
                ".".join(str(part) for part in error["loc"]) or "args": [error["msg"]]
                for error in exc.errors()
            }
            raise ToolRejected(
                "The tool arguments are invalid.",
                code="invalid_arguments",
                field_errors=fields,
            ) from exc

    # -- gates ------------------------------------------------------------- #

    def _resolve_membership(self, ctx: ToolContext) -> tuple[Any, Any]:
        user = ctx.user or getattr(ctx.call, "started_by", None)
        membership = ctx.membership
        if membership is None and user is not None and ctx.clinic is not None:
            from apps.clinics.models import Membership

            membership = Membership.all_objects.filter(
                clinic=ctx.clinic, user=user, status="active"
            ).first()
        return user, membership

    def _check_permission(self, ctx: ToolContext, tool: Tool) -> None:
        if not tool.required_perms:
            return
        user, membership = self._resolve_membership(ctx)
        try:
            for perm in tool.required_perms:
                require_perm(user, membership, perm)
        except ApiError as exc:
            raise ToolRejected(
                "You do not have permission to use this tool.",
                code="permission_denied",
            ) from exc

    def _check_confirmation(self, ctx: ToolContext, tool: Tool) -> None:
        call = ctx.call
        if call is None:
            raise ToolRejected(
                "This action needs an explicit confirmation.",
                code="confirmation_required",
            )
        if not ctx.confirmed:
            nonce = ensure_confirmation_request(call, tool.name)
            raise ToolRejected(
                "This action needs an explicit confirmation.",
                code="confirmation_required",
                details={"confirmation_nonce": nonce},
            )
        if not call.confirmation_nonce or ctx.confirmation_nonce != call.confirmation_nonce:
            raise ToolRejected(
                "This confirmation is not valid for this call.",
                code="confirmation_invalid",
            )
        requested = VoiceTurn.all_objects.filter(
            call=call,
            role=VoiceTurn.Role.ASSISTANT,
            kind=VoiceTurn.Kind.CONFIRMATION_REQUEST,
        ).exists()
        if not requested:
            raise ToolRejected(
                "The assistant has not asked for confirmation yet.",
                code="confirmation_required",
            )

    # -- recording --------------------------------------------------------- #

    def _record(
        self,
        ctx: ToolContext,
        tool_name: str,
        args_hash: str,
        status: str,
        error_code: str,
        result: Any,
        latency_ms: int,
        *,
        raw_args: Any = None,
        turn: Any = None,
    ) -> ToolResult:
        invocation = ToolInvocation.all_objects.create(
            clinic=ctx.clinic,
            user=getattr(ctx, "user", None) or getattr(ctx.call, "started_by", None),
            call=ctx.call,
            tool=tool_name,
            args_hash=args_hash,
            status=status,
            error_code=error_code or "",
            latency_ms=latency_ms,
            result_json=_json_safe(result),
        )
        if ctx.call is not None:
            VoiceToolCall.all_objects.create(
                clinic=ctx.clinic,
                call=ctx.call,
                turn=turn,
                tool=tool_name,
                args_json=_json_safe(raw_args) if raw_args is not None else {},
                confirmed=bool(ctx.confirmed),
                result_json=_json_safe(result),
                status=status,
            )
        return ToolResult(
            tool=tool_name,
            status=status,
            result=_json_safe(result),
            error_code=error_code or "",
            details={"invocation_id": str(invocation.id)},
        )

    # -- execution --------------------------------------------------------- #

    def execute(
        self,
        ctx: ToolContext,
        tool_name: str,
        raw_args: Any = None,
        *,
        confirmed: bool = False,
        confirmation_nonce: str = "",
        turn: Any = None,
    ) -> ToolResult:
        started = time.perf_counter()
        args_hash = hash_args(raw_args)
        ctx = ctx.with_confirmation(confirmed=bool(confirmed), nonce=confirmation_nonce or "")

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:
            tool = self.get(tool_name)
        except ToolRejected as exc:
            result = self._record(
                ctx, str(tool_name or ""), args_hash, "rejected", exc.code, None, elapsed(),
                raw_args=raw_args, turn=turn,
            )
            result.details.update(exc.details)
            return result

        try:
            args = self.validate_args(tool, raw_args)
        except ToolRejected as exc:
            result = self._record(
                ctx, tool.name, args_hash, "rejected", exc.code, None, elapsed(),
                raw_args=raw_args, turn=turn,
            )
            if exc.field_errors:
                result.details["field_errors"] = exc.field_errors
            return result

        try:
            self._check_permission(ctx, tool)
            if tool.requires_patient and ctx.patient is None:
                raise ToolRejected(
                    "The caller's identity must be confirmed first.",
                    code="identity_required",
                )
            if tool.consequential:
                self._check_confirmation(ctx, tool)
        except ToolRejected as exc:
            result = self._record(
                ctx, tool.name, args_hash, "rejected", exc.code, None, elapsed(),
                raw_args=raw_args, turn=turn,
            )
            result.details.update(exc.details)
            return result

        try:
            with transaction.atomic():
                result_payload = tool.execute(ctx, args)
        except ToolRejected as exc:
            result = self._record(
                ctx, tool.name, args_hash, "rejected", exc.code, None, elapsed(),
                raw_args=raw_args, turn=turn,
            )
            result.details.update(exc.details)
            return result
        except ApiError as exc:
            return self._record(
                ctx, tool.name, args_hash, "rejected", exc.code, None, elapsed(),
                raw_args=raw_args, turn=turn,
            )
        except Exception:  # noqa: BLE001 - any unexpected failure is a tool error
            # The transaction has rolled back: no partial write remains. Escalate
            # instead of surfacing a 500 to the caller.
            if ctx.call is not None:
                from ..services.escalation import escalate_call

                escalate_call(ctx.call, reason_code="tool_failure", actor=ctx.actor)
            return self._record(
                ctx, tool.name, args_hash, "error", "tool_error", None, elapsed(),
                raw_args=raw_args, turn=turn,
            )

        if tool.consequential and ctx.call is not None:
            clear_confirmation(ctx.call)

        return self._record(
            ctx, tool.name, args_hash, "ok", "", result_payload, elapsed(),
            raw_args=raw_args, turn=turn,
        )


def _build_default_registry() -> ToolRegistry:
    from .availability import TOOL as availability_tool
    from .booking import TOOL as booking_tool
    from .cancel import TOOL as cancel_tool
    from .escalate import TOOL as escalate_tool
    from .faq import TOOL as faq_tool
    from .reschedule import TOOL as reschedule_tool

    return ToolRegistry(
        [
            availability_tool,
            booking_tool,
            reschedule_tool,
            cancel_tool,
            faq_tool,
            escalate_tool,
        ]
    )


_DEFAULT: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = _build_default_registry()
    return _DEFAULT


__all__ = ["ToolRegistry", "get_registry", "hash_args"]
