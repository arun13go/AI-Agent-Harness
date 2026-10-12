"""Provide a deterministic act-tier tool for approval lifecycle tests."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from agent_harness.core.contracts import ActionProposer
from agent_harness.core.errors import ActionOutcomeUnknownError, InputValidationError
from agent_harness.core.types import (
    RequestContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolRiskTier,
)


class MaintenanceProposalTool:
    """Create a reviewable maintenance proposal without external mutation."""

    def __init__(self, proposer: ActionProposer) -> None:
        self._proposer = proposer

    @property
    def definition(self) -> ToolDefinition:
        """Describe the proposal input and propose-tier risk."""

        return ToolDefinition(
            name="propose_maintenance_request",
            description="Propose a maintenance request for human approval",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["title"],
                "properties": {
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    }
                },
            },
            risk_tier=ToolRiskTier.PROPOSE,
        )

    async def invoke(
        self,
        *,
        context: RequestContext,
        call: ToolCall,
        idempotency_key: str | None,
    ) -> ToolResult:
        """Create a checkpointed act-tier proposal without performing it."""

        pending = await self._proposer.propose(
            authenticated_identity=context.identity,
            context=context,
            call=ToolCall(
                call_id=f"action-{call.call_id}",
                tool_name="create_maintenance_request",
                arguments=call.arguments,
            ),
        )
        return ToolResult(
            call_id=call.call_id,
            value={
                "status": "waiting-for-approval",
                "proposalId": pending.proposal_id,
                "checkpointId": pending.checkpoint_id,
                "approvalId": pending.approval_id,
            },
        )


class MaintenanceRequestTool:
    """Create one local maintenance request per idempotency key."""

    def __init__(
        self,
        *,
        request_id_factory: Callable[[], str] | None = None,
        lose_response: bool = False,
    ) -> None:
        self._request_id_factory = request_id_factory or (lambda: f"request-{uuid4().hex}")
        self._lose_response = lose_response
        self._results: dict[str, ToolResult] = {}
        self.effect_count = 0

    @property
    def definition(self) -> ToolDefinition:
        """Describe the action's input and act-tier risk."""

        return ToolDefinition(
            name="create_maintenance_request",
            description="Create a local maintenance request after approval",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["title"],
                "properties": {
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    }
                },
            },
            risk_tier=ToolRiskTier.ACT,
        )

    async def invoke(
        self,
        *,
        context: RequestContext,
        call: ToolCall,
        idempotency_key: str | None,
    ) -> ToolResult:
        """Create or replay one idempotent local maintenance request."""

        if idempotency_key is None:
            raise InputValidationError("Act-tier tools require an idempotency key")
        existing = self._results.get(idempotency_key)
        if existing is not None:
            return existing

        title = call.arguments.get("title")
        if not isinstance(title, str):
            raise InputValidationError("Maintenance request requires a string 'title'")
        result = ToolResult(
            call_id=call.call_id,
            value={
                "requestId": self._request_id_factory(),
                "status": "created",
                "title": title,
            },
        )
        self._results[idempotency_key] = result
        self.effect_count += 1
        if self._lose_response:
            raise ActionOutcomeUnknownError("Action completed but its response was lost")
        return result
