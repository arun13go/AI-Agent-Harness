"""Let MAF evaluators score runs that go through the governed executor.

MAF evaluation is experimental, so nothing on the serving path imports this module; evaluation is
opt-in by construction. Every query uses ``MafAgentExecutor`` (approvals, identity scope, audit).
By default only answer text is returned. Explicit ``capture_transcript=True`` retains the real
SDK response, including tool arguments and results, for an approved evaluation. It does not add
payloads to audit or telemetry. ``to_eval_item`` includes the same trusted instructions passed to
the executor; evaluators may send those instructions to their configured provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, overload
from uuid import uuid4

from agent_framework import (
    AgentResponse,
    AgentResponseUpdate,
    AgentRunInputs,
    AgentSession,
    ChatOptions,
    Content,
    EvalItem,
    FunctionTool,
    ResponseStream,
    ServiceSessionId,
    SupportsChatGetResponse,
)
from agent_framework import Message as MafMessage

from agent_harness.adapters.maf.agent_executor import MafAgentExecutor
from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.contracts import AuditSink, Tool
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    IdentityScope,
    Message,
    ModelRequest,
    RequestContext,
)


class GovernedEvalAgent:
    """A MAF ``SupportsAgentRun`` view of ``MafAgentExecutor`` for evaluation."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        client: SupportsChatGetResponse[ChatOptions],
        tools: Registry[Tool],
        audit_sink: AuditSink,
        identity: IdentityScope,
        instructions: Sequence[str] = (),
        max_output_tokens: int = 1024,
        capture_transcript: bool = False,
    ) -> None:
        self.id = "agent-harness-eval"
        self.name: str | None = "agent-harness"
        self.description: str | None = None
        self._eval_tools = [
            FunctionTool(
                name=tool.definition.name,
                description=tool.definition.description,
                input_model=tool.definition.input_schema,
            )
            for _, tool in tools
        ]
        self.default_options: dict[str, Any] = {"tools": self._eval_tools}
        self._executor = MafAgentExecutor(
            config=config, client=client, tools=tools, audit_sink=audit_sink
        )
        self._capture_transcript = capture_transcript
        self._tool_definitions = tuple(tool.definition for _, tool in tools)
        self._timeout = timedelta(seconds=config.runtime.timeout_seconds)
        self._identity = identity
        self._instructions = tuple(instructions)
        self._max_output_tokens = max_output_tokens

    @overload
    def run(
        self,
        messages: AgentRunInputs | None = None,
        *,
        stream: Literal[True],
        session: AgentSession | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> ResponseStream[AgentResponseUpdate, AgentResponse[Any]]: ...

    @overload
    def run(
        self,
        messages: AgentRunInputs | None = None,
        *,
        stream: Literal[False] = False,
        session: AgentSession | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> Awaitable[AgentResponse[Any]]: ...

    def run(
        self,
        messages: AgentRunInputs | None = None,
        *,
        stream: bool = False,
        session: AgentSession | None = None,
        function_invocation_kwargs: Mapping[str, Any] | None = None,
        client_kwargs: Mapping[str, Any] | None = None,
    ) -> Awaitable[AgentResponse[Any]] | ResponseStream[AgentResponseUpdate, AgentResponse[Any]]:
        if stream or session is not None or function_invocation_kwargs or client_kwargs:
            raise ValueError("The evaluation agent runs one plain turn without a session")
        return self._run(messages)

    async def _run(self, messages: AgentRunInputs | None) -> AgentResponse[None]:
        context = RequestContext(
            identity=self._identity,
            run_id=f"eval-{uuid4().hex}",
            correlation_id=f"eval-{uuid4().hex}",
            trace_id=uuid4().hex,
            deadline=datetime.now(UTC) + self._timeout,
        )
        request = ModelRequest(
            context=context,
            messages=(
                *(Message("system", text, "trusted-instruction") for text in self._instructions),
                *_user_messages(messages),
            ),
            tools=self._tool_definitions,
            max_output_tokens=self._max_output_tokens,
        )
        captured: list[AgentResponse[None]] = []
        response = await self._executor.run(
            authenticated_identity=self._identity,
            request=request,
            on_response=captured.append if self._capture_transcript else None,
        )
        if self._capture_transcript:
            if len(captured) != 1:
                raise RuntimeError("Evaluation did not receive exactly one completed SDK response")
            return captured[0]
        text = response.message.content if response.message else ""
        return AgentResponse(messages=[MafMessage("assistant", [text])])

    def to_eval_item(
        self, *, query: str, response: AgentResponse[Any], expected_output: str | None = None
    ) -> EvalItem:
        """Build a judge item with trusted instructions and an opt-in captured response."""
        if not self._capture_transcript:
            raise ValueError("Evaluation items require transcript capture")
        return EvalItem(
            conversation=[
                MafMessage("system", [self._executor.compose_instructions(self._instructions)]),
                MafMessage("user", [query]),
                *response.messages,
            ],
            tools=self._eval_tools,
            expected_output=expected_output,
        )

    def create_session(self, *, session_id: str | None = None) -> AgentSession:
        return AgentSession(session_id=session_id)

    def get_session(
        self, service_session_id: str | ServiceSessionId, *, session_id: str | None = None
    ) -> AgentSession:
        return AgentSession(service_session_id=service_session_id, session_id=session_id)


def _user_messages(messages: AgentRunInputs | None) -> tuple[Message, ...]:
    items = [messages] if isinstance(messages, (str, Content, MafMessage)) else list(messages or [])
    converted: list[Message] = []
    for item in items:
        if isinstance(item, MafMessage) and item.role == "user":
            text = item.text
        elif isinstance(item, str):
            text = item
        else:
            raise ValueError("Only user messages can be evaluated")
        converted.append(Message("user", text, "trusted-user-input"))
    return tuple(converted)
