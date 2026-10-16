"""Execute governed read tools through registered policy and audit."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.contracts import AuditSink, Tool
from agent_harness.core.errors import (
    ExecutionTimeoutError,
    HarnessError,
    ToolExecutionError,
)
from agent_harness.core.event_writer import RunEventWriter
from agent_harness.core.governance import (
    ExecutionBudget,
    authorize_tool,
    validate_request_context,
    validate_tool_call,
)
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    IdentityScope,
    RequestContext,
    ToolCall,
    ToolResult,
    ToolRiskTier,
)


class ReadToolExecutor:
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
        self._clock = clock
        self._event_id_factory = event_id_factory

    async def run(
        self,
        *,
        authenticated_identity: IdentityScope,
        context: RequestContext,
        call: ToolCall,
    ) -> ToolResult:
        """Execute one registered tool under the configured policy."""

        writer = RunEventWriter(
            context=context,
            audit_sink=self._audit_sink,
            clock=self._clock,
            event_id_factory=self._event_id_factory,
        )
        now = self._clock() if self._clock is not None else None
        validate_request_context(authenticated_identity, context, now=now)
        budget = ExecutionBudget(
            max_steps=self._config.runtime.max_steps,
            max_tool_calls=self._config.runtime.max_tool_calls,
        )
        await writer.emit("run.started", source="app", outcome="started", attributes={})
        try:
            result = await self._execute_tool(
                context=context,
                call=call,
                writer=writer,
                budget=budget,
            )
            await writer.emit("run.completed", source="app", outcome="succeeded", attributes={})
            return result
        except HarnessError as error:
            await writer.emit(
                "run.failed",
                source="app",
                outcome="failed",
                attributes={"errorCode": error.code},
            )
            raise

    async def run_tool(
        self,
        *,
        authenticated_identity: IdentityScope,
        context: RequestContext,
        call: ToolCall,
        budget: ExecutionBudget,
    ) -> ToolResult:
        """Execute one governed tool inside a caller-owned run lifecycle."""

        now = self._clock() if self._clock is not None else None
        validate_request_context(authenticated_identity, context, now=now)
        return await self._execute_tool(
            context=context,
            call=call,
            writer=RunEventWriter(
                context=context,
                audit_sink=self._audit_sink,
                clock=self._clock,
                event_id_factory=self._event_id_factory,
            ),
            budget=budget,
        )

    async def _execute_tool(
        self,
        *,
        context: RequestContext,
        call: ToolCall,
        writer: RunEventWriter,
        budget: ExecutionBudget,
    ) -> ToolResult:
        """Execute a validated tool and emit only its lifecycle events."""

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

            await writer.emit(
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
            current_time = (
                self._clock() if self._clock is not None else datetime.now(context.deadline.tzinfo)
            )
            remaining = (context.deadline - current_time).total_seconds()
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

            await writer.emit(
                "tool.completed",
                source="tool",
                outcome="succeeded",
                attributes={
                    "toolName": tool.definition.name,
                    "callId": call.call_id,
                    "riskTier": tool.definition.risk_tier.value,
                },
            )
            return result
        except HarnessError as error:
            if tool_started:
                await writer.emit(
                    "tool.failed",
                    source="tool",
                    outcome="failed",
                    attributes={"callId": call.call_id, "errorCode": error.code},
                )
            raise
        except Exception as error:
            normalized = ToolExecutionError("Tool execution failed")
            if tool_started:
                await writer.emit(
                    "tool.failed",
                    source="tool",
                    outcome="failed",
                    attributes={"callId": call.call_id, "errorCode": normalized.code},
                )
            raise normalized from error
