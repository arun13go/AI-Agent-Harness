# pyright: reportUnknownMemberType=false

"""Enforce identity, authorization, input, deadline, and execution policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from jsonschema import Draft202012Validator

from agent_harness.core.errors import (
    AuthorizationError,
    ExecutionLimitError,
    ExecutionTimeoutError,
    InputValidationError,
)
from agent_harness.core.types import (
    IdentityScope,
    RequestContext,
    ToolCall,
    ToolDefinition,
    ToolRiskTier,
)

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_TIER_ORDER = {
    ToolRiskTier.READ: 0,
    ToolRiskTier.PROPOSE: 1,
    ToolRiskTier.ACT: 2,
}


@dataclass(slots=True)
class ExecutionBudget:
    """Track bounded steps and tool calls for one run."""

    max_steps: int
    max_tool_calls: int
    steps: int = 0
    tool_calls: int = 0

    def consume_step(self) -> None:
        """Consume one step or reject the run at its limit."""

        if self.steps >= self.max_steps:
            raise ExecutionLimitError(f"Run exceeded its {self.max_steps} step limit")
        self.steps += 1

    def consume_tool_call(self) -> None:
        """Consume one tool call or reject the run at its limit."""

        if self.tool_calls >= self.max_tool_calls:
            raise ExecutionLimitError(f"Run exceeded its {self.max_tool_calls} tool-call limit")
        self.tool_calls += 1


def validate_request_context(
    authenticated_identity: IdentityScope,
    context: RequestContext,
    *,
    now: datetime | None = None,
) -> None:
    """Validate identifiers, identity binding, and deadline."""

    for label, value in (
        ("tenant scope", context.identity.tenant_scope),
        ("subject scope", context.identity.subject_scope),
        ("run ID", context.run_id),
        ("correlation ID", context.correlation_id),
    ):
        if not _OPAQUE_ID_PATTERN.fullmatch(value):
            raise InputValidationError(f"{label.capitalize()} is not a valid opaque identifier")
    if not re.fullmatch(r"[0-9a-f]{32}", context.trace_id):
        raise InputValidationError("Trace ID must contain 32 lowercase hexadecimal characters")
    if authenticated_identity != context.identity:
        raise AuthorizationError("Authenticated identity does not match the request context")
    if context.deadline.tzinfo is None or context.deadline.utcoffset() is None:
        raise InputValidationError("Request deadline must be timezone-aware")
    if context.deadline <= (now or datetime.now(UTC)):
        raise ExecutionTimeoutError("Request deadline has expired")


def authorize_tool(maximum_tier: ToolRiskTier, definition: ToolDefinition) -> None:
    """Reject tools above the profile's permission tier."""

    if _TIER_ORDER[definition.risk_tier] > _TIER_ORDER[maximum_tier]:
        raise AuthorizationError(
            f"Tool '{definition.name}' requires tier '{definition.risk_tier}' "
            f"but the profile permits '{maximum_tier}'"
        )


def validate_tool_call(definition: ToolDefinition, call: ToolCall) -> None:
    """Validate a call against its registered tool schema."""

    if call.tool_name != definition.name:
        raise InputValidationError(
            f"Tool call targets '{call.tool_name}' but resolved definition is '{definition.name}'"
        )
    errors = sorted(
        Draft202012Validator(definition.input_schema).iter_errors(call.arguments),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise InputValidationError(f"Invalid input for tool '{definition.name}': {details}")
