"""Run one governed local read call through registered tools and audit."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.contracts import AuditSink, Tool
from agent_harness.core.errors import (
    ExecutionTimeoutError,
    HarnessError,
    ToolExecutionError,
)
from agent_harness.core.governance import (
    ExecutionBudget,
    authorize_tool,
    validate_request_context,
    validate_tool_call,
)
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    IdentityScope,
    JsonScalar,
    RequestContext,
    RunEvent,
    ToolCall,
    ToolResult,
    ToolRiskTier,
)


class LocalReadRunner:
    """Coordinate validation, tool execution, limits, and lifecycle events."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        tools: Registry[Tool],
        audit_sink: AuditSink,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._audit_sink = audit_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (lambda: uuid4().hex)

    async def run(
        self,
        *,
        authenticated_identity: IdentityScope,
        context: RequestContext,
        call: ToolCall,
    ) -> ToolResult:
        """Execute one registered tool under the configured policy."""

        validate_request_context(authenticated_identity, context, now=self._clock())
        budget = ExecutionBudget(
            max_steps=self._config.runtime.max_steps,
            max_tool_calls=self._config.runtime.max_tool_calls,
        )
        sequence = 0

        async def emit(
            event_type: str,
            *,
            source: str,
            outcome: str,
            attributes: dict[str, JsonScalar | list[JsonScalar]],
        ) -> None:
            nonlocal sequence
            await self._audit_sink.append(
                RunEvent(
                    schema_version=1,
                    event_id=self._event_id_factory(),
                    event_type=event_type,
                    occurred_at=self._clock(),
                    sequence=sequence,
                    context=context,
                    source=source,
                    outcome=outcome,
                    attributes=attributes,
                )
            )
            sequence += 1

        await emit("run.started", source="app", outcome="started", attributes={})
        tool_started = False
        try:
            budget.consume_step()
            tool = self._tools.resolve(call.tool_name)
            authorize_tool(
                ToolRiskTier(self._config.governance.maximum_tier.value),
                tool.definition,
            )
            validate_tool_call(tool.definition, call)
            budget.consume_tool_call()

            await emit(
                "tool.started",
                source="tool",
                outcome="started",
                attributes={
                    "toolName": tool.definition.name,
                    "callId": call.call_id,
                    "riskTier": tool.definition.risk_tier.value,
                },
            )
            tool_started = True
            remaining = (context.deadline - self._clock()).total_seconds()
            timeout_seconds = min(float(self._config.runtime.timeout_seconds), remaining)
            if timeout_seconds <= 0:
                raise ExecutionTimeoutError("Request deadline expired before tool execution")
            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await tool.invoke(
                        context=context,
                        call=call,
                        idempotency_key=None,
                    )
            except TimeoutError as error:
                raise ExecutionTimeoutError("Tool execution exceeded its deadline") from error

            await emit(
                "tool.completed",
                source="tool",
                outcome="succeeded",
                attributes={
                    "toolName": tool.definition.name,
                    "callId": call.call_id,
                    "riskTier": tool.definition.risk_tier.value,
                },
            )
            await emit("run.completed", source="app", outcome="succeeded", attributes={})
            return result
        except HarnessError as error:
            if tool_started:
                await emit(
                    "tool.failed",
                    source="tool",
                    outcome="failed",
                    attributes={"callId": call.call_id, "errorCode": error.code},
                )
            await emit(
                "run.failed",
                source="app",
                outcome="failed",
                attributes={"errorCode": error.code},
            )
            raise
        except Exception as error:
            normalized = ToolExecutionError("Tool execution failed")
            if tool_started:
                await emit(
                    "tool.failed",
                    source="tool",
                    outcome="failed",
                    attributes={"callId": call.call_id, "errorCode": normalized.code},
                )
            await emit(
                "run.failed",
                source="app",
                outcome="failed",
                attributes={"errorCode": normalized.code},
            )
            raise normalized from error
