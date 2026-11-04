# pyright: reportUnknownMemberType=false

"""Enforce identity, authorization, input, deadline, and execution policy."""

from __future__ import annotations

import re
from collections.abc import Sequence
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
    SourceReference,
    ToolCall,
    ToolDefinition,
    ToolRiskTier,
)

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_AUTHORIZATION_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MAX_AUTHORIZATION_LABELS = 64
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
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

    validate_identity_scope(context.identity)
    validate_identity_scope(authenticated_identity)
    for label, value in (
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


def validate_identity_scope(identity: IdentityScope) -> None:
    """Validate tenant and subject identifiers without authorizing them."""

    for label, value in (
        ("tenant scope", identity.tenant_scope),
        ("subject scope", identity.subject_scope),
    ):
        if not _OPAQUE_ID_PATTERN.fullmatch(value):
            raise InputValidationError(f"{label.capitalize()} is not a valid opaque identifier")


def validate_tenant_scope(tenant_scope: str) -> None:
    """Validate a tenant identifier supplied outside an identity, such as at ingestion."""

    if not _OPAQUE_ID_PATTERN.fullmatch(tenant_scope):
        raise InputValidationError("Tenant scope is not a valid opaque identifier")


def validate_authorization_labels(labels: Sequence[str]) -> tuple[str, ...]:
    """Validate labels and return them de-duplicated in canonical order."""

    canonical = tuple(sorted(set(labels)))
    if len(canonical) > _MAX_AUTHORIZATION_LABELS:
        raise InputValidationError(
            f"At most {_MAX_AUTHORIZATION_LABELS} distinct authorization labels are allowed"
        )
    if any(not _AUTHORIZATION_LABEL_PATTERN.fullmatch(label) for label in canonical):
        raise InputValidationError("Authorization label is invalid")
    return canonical


def validate_source_reference(source: SourceReference) -> None:
    """Validate source lineage so chunk identity and citations stay unambiguous."""

    for label, value, limit in (
        ("Source ID", source.source_id, 256),
        ("Source version", source.source_version, 256),
        ("Source path", source.structural_path, 512),
    ):
        if not 1 <= len(value) <= limit or _CONTROL_CHARACTERS.search(value):
            raise InputValidationError(
                f"{label} must contain 1 to {limit} characters without control characters"
            )
    if "#" in source.structural_path:
        raise InputValidationError("Source path must not contain '#', which separates chunk paths")
    for label, moment in (
        ("Ingestion time", source.ingested_at),
        ("Freshness deadline", source.fresh_until),
    ):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise InputValidationError(f"{label} must be timezone-aware")


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
