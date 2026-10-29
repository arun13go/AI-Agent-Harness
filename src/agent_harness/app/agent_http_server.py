"""Serve agent requests over HTTP through the governed MAF executor.

Use the released Responses SDK for JSON and streamed answers. Enforce caller
authentication and request limits before execution, and handle cancellation.
Start locally with a loopback-only identity or in Foundry with platform identity.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agent_framework import ChatOptions, SupportsChatGetResponse
from azure.ai.agentserver.responses import (
    InMemoryResponseProvider,
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
)
from azure.ai.agentserver.responses.models import CreateResponse, ResponseStreamEvent
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.types import Message as ASGIMessage

from agent_harness.adapters.foundry.model_client import (
    create_foundry_chat_client,
    create_foundry_credential,
)
from agent_harness.adapters.foundry.request_identity import FoundryIngressIdentity
from agent_harness.adapters.foundry.telemetry import create_telemetry_provider
from agent_harness.adapters.maf.agent_executor import MafAgentExecutor
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.adapters.tools.service_status import ServiceStatusTool
from agent_harness.core.configuration import (
    HarnessConfig,
    ObservabilityConfig,
    ProfileName,
    load_harness_config,
)
from agent_harness.core.contracts import AuditSink, Tool
from agent_harness.core.errors import (
    AuthorizationError,
    ConfigurationError,
    HarnessError,
    ModelProviderError,
)
from agent_harness.core.governance import validate_identity_scope
from agent_harness.core.registry import Registry
from agent_harness.core.types import IdentityScope, Message, ModelRequest, RequestContext

_LOGGER = logging.getLogger(__name__)
_MAX_BODY_BYTES = 65_536
Authenticator = Callable[[Request], Awaitable[IdentityScope]]


class _InputText(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["input_text"]
    text: str = Field(min_length=1, max_length=_MAX_BODY_BYTES)


class _UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user"]
    type: Literal["message"] = "message"
    content: str | list[_InputText] = Field(min_length=1, max_length=_MAX_BODY_BYTES)


class _AgentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["agent_reference"]
    name: Literal["agent-harness"]
    version: str | None = Field(default=None, pattern=r"^[1-9][0-9]{0,9}$")


class _CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input: str | list[_UserMessage] = Field(min_length=1, max_length=_MAX_BODY_BYTES)
    model: Literal["agent-harness"] = "agent-harness"
    stream: bool = False
    store: Literal[False] = False
    background: Literal[False] = False
    max_output_tokens: int = Field(default=1024, ge=1, le=4096)
    agent_reference: _AgentReference | None = None
    agent_session_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$", max_length=128
    )

    def messages(self) -> tuple[Message, ...]:
        if isinstance(self.input, str):
            return (Message("user", self.input, "trusted-user-input"),)
        return tuple(
            Message(
                "user",
                item.content
                if isinstance(item.content, str)
                else "\n".join(part.text for part in item.content),
                "trusted-user-input",
            )
            for item in self.input
        )


@dataclass(frozen=True)
class _AcceptedRequest:
    identity: IdentityScope
    body: _CreateRequest


_ACCEPTED: ContextVar[_AcceptedRequest] = ContextVar("harness_http_request")


class _RequestBoundary:
    """Authenticate and bound stateless requests before the SDK handles them."""

    def __init__(
        self, app: ASGIApp, *, authenticate: Authenticator, provider: TracerProvider | None = None
    ) -> None:
        self.app = app
        self.authenticate = authenticate
        self.tracer = (provider or trace.get_tracer_provider()).get_tracer(__name__)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/readiness":
            await self._handle(scope, receive, send)
            return
        carrier = dict(Request(scope).headers)
        parent = TraceContextTextMapPropagator().extract(carrier)
        with self.tracer.start_as_current_span(
            "harness.http",
            context=parent,
            kind=trace.SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            forwarded: dict[str, str] = {}
            TraceContextTextMapPropagator().inject(forwarded)
            sdk_scope = dict(scope)
            sdk_scope["headers"] = [
                (key, value)
                for key, value in scope["headers"]
                if key.lower() not in {b"traceparent", b"tracestate", b"baggage"}
            ] + [(key.encode("ascii"), value.encode("ascii")) for key, value in forwarded.items()]

            async def traced_send(message: ASGIMessage) -> None:
                if message["type"] == "http.response.start":
                    span.set_attribute("http.response.status_code", message["status"])
                    if message["status"] >= 400:
                        span.set_status(trace.Status(trace.StatusCode.ERROR))
                await send(message)

            await self._handle(sdk_scope, receive, traced_send)

    async def _handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.url.path == "/readiness" and request.method == "GET":
            await self.app(scope, receive, send)
            return
        if request.url.path != "/responses" or request.method != "POST":
            await self._reject(scope, receive, send, 404, "unsupported_endpoint")
            return
        try:
            identity = await self.authenticate(request)
            validate_identity_scope(identity)
        except AuthorizationError:
            await self._reject(scope, receive, send, 401, "authentication_required")
            return
        except HarnessError:
            await self._reject(scope, receive, send, 403, "invalid_identity")
            return
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            await self._reject(scope, receive, send, 415, "json_required")
            return
        payload = bytearray()
        try:
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > _MAX_BODY_BYTES:
                    await self._reject(scope, receive, send, 413, "request_too_large")
                    return
        except ClientDisconnect:
            _LOGGER.warning("HTTP request rejected: client_disconnected")
            return
        try:
            body = _CreateRequest.model_validate_json(payload)
        except ValidationError as error:
            known_fields = {
                "input",
                "model",
                "stream",
                "store",
                "background",
                "max_output_tokens",
                "role",
                "type",
                "content",
                "text",
                "id",
                "status",
                "instructions",
                "tools",
                "conversation",
                "previous_response_id",
                "metadata",
                "user",
                "stream_options",
                "parallel_tool_calls",
                "tool_choice",
                "include",
                "temperature",
                "top_p",
                "agent_reference",
                "agent_session_id",
                "name",
                "version",
            }
            for issue in error.errors(include_input=False, include_context=False)[:8]:
                fields = [
                    part if part in known_fields else "other"
                    for part in issue["loc"]
                    if isinstance(part, str)
                ]
                _LOGGER.warning("HTTP schema rejection at fields: %s", ".".join(fields))
            await self._reject(scope, receive, send, 400, "invalid_request")
            return
        normalized = body.model_dump_json(exclude={"agent_reference", "agent_session_id"}).encode()
        delivered = False

        async def replay() -> ASGIMessage:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": normalized, "more_body": False}
            return await receive()

        token = _ACCEPTED.set(_AcceptedRequest(identity, body))
        try:
            await self.app(scope, replay, send)
        finally:
            _ACCEPTED.reset(token)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
        _LOGGER.warning("HTTP request rejected: %s", code)
        await JSONResponse({"error": {"code": code, "message": code}}, status_code=status)(
            scope, receive, send
        )


def create_responses_host(
    *,
    config: HarnessConfig,
    client: SupportsChatGetResponse[ChatOptions],
    tools: Registry[Tool],
    audit_sink: AuditSink,
    authenticate: Authenticator,
    telemetry_provider: TracerProvider | None = None,
) -> ResponsesAgentServerHost:
    """Compose a stateless Responses host with an explicit trusted authenticator."""

    if config.profile is not ProfileName.ASSISTANT:
        raise ConfigurationError("The HTTP host currently supports only the assistant profile")
    if config.observability.exporter == "application-insights" and telemetry_provider is None:
        raise ConfigurationError(
            "Application Insights requires an explicitly composed telemetry provider"
        )
    app = ResponsesAgentServerHost(
        store=InMemoryResponseProvider(),
        options=ResponsesServerOptions(resilient_background=False),
        configure_observability=None,
        access_log=None,
    )
    app.add_middleware(_RequestBoundary, authenticate=authenticate, provider=telemetry_provider)
    executor = MafAgentExecutor(config=config, client=client, tools=tools, audit_sink=audit_sink)

    @app.response_handler
    async def handle(
        request: CreateResponse, context: ResponseContext, cancellation_signal: asyncio.Event
    ) -> AsyncIterator[ResponseStreamEvent]:
        accepted = _ACCEPTED.get()
        active_trace = trace.get_current_span().get_span_context()
        harness_context = RequestContext(
            identity=accepted.identity,
            run_id=f"run-{uuid4().hex}",
            correlation_id=f"correlation-{uuid4().hex}",
            trace_id=f"{active_trace.trace_id:032x}" if active_trace.is_valid else uuid4().hex,
            deadline=datetime.now(UTC) + timedelta(seconds=config.runtime.timeout_seconds),
        )
        model_request = ModelRequest(
            context=harness_context,
            messages=accepted.body.messages(),
            tools=tuple(tool.definition for _, tool in tools),
            max_output_tokens=accepted.body.max_output_tokens,
        )
        stream = ResponseEventStream(response_id=context.response_id, request=request)
        yield stream.emit_created()
        yield stream.emit_in_progress()
        current = asyncio.current_task()
        assert current is not None

        async def cancel_on_signal() -> None:
            cancel = asyncio.create_task(cancellation_signal.wait())
            shutdown = asyncio.create_task(context.shutdown.wait())
            try:
                await asyncio.wait((cancel, shutdown), return_when=asyncio.FIRST_COMPLETED)
                current.cancel()
            finally:
                cancel.cancel()
                shutdown.cancel()
                await asyncio.gather(cancel, shutdown, return_exceptions=True)

        watcher = asyncio.create_task(cancel_on_signal())
        try:
            if accepted.body.stream:
                message = stream.add_output_item_message()
                yield message.emit_added()
                text = message.add_text_content()
                yield text.emit_added()
                chunks: list[str] = []
                terminal = None
                async for response in executor.stream(
                    authenticated_identity=accepted.identity, request=model_request
                ):
                    if response.message is not None:
                        chunks.append(response.message.content)
                        yield text.emit_delta(response.message.content)
                    else:
                        terminal = response
                if terminal is None:
                    raise ModelProviderError("Model stream ended without terminal metadata")
                response = terminal
                yield text.emit_text_done("".join(chunks))
                yield text.emit_done()
                yield message.emit_done()
            else:
                response = await executor.run(
                    authenticated_identity=accepted.identity, request=model_request
                )
                if response.message is not None:
                    for event in stream.output_item_message(response.message.content):
                        yield event
            yield stream.emit_completed(
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                    "input_tokens_details": {"cached_tokens": response.usage.cached_input_tokens},
                    "output_tokens_details": {"reasoning_tokens": 0},
                }
            )
        except HarnessError as error:
            _LOGGER.warning("HTTP model execution failed: %s", error.code)
            yield stream.emit_failed(code=error.code, message="Governed agent execution failed")
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    return app


def create_foundry_responses_host(
    *,
    config: HarnessConfig,
    client: SupportsChatGetResponse[ChatOptions],
    tools: Registry[Tool],
    audit_sink: AuditSink,
    environment: Mapping[str, str] | None = None,
) -> ResponsesAgentServerHost:
    """Compose identity mapping exclusively for Foundry's isolated ingress."""

    return create_responses_host(
        config=config,
        client=client,
        tools=tools,
        audit_sink=audit_sink,
        authenticate=FoundryIngressIdentity(environment=environment),
    )


def create_local_authenticator(
    identity: IdentityScope, *, environment: Mapping[str, str] | None = None
) -> Authenticator:
    """Bind development identity to loopback, refusing Foundry-hosted execution."""

    values = os.environ if environment is None else environment
    if values.get("FOUNDRY_HOSTING_ENVIRONMENT"):
        raise ConfigurationError("Local development identity cannot run in a hosted deployment")
    validate_identity_scope(identity)

    async def authenticate(request: Request) -> IdentityScope:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise AuthorizationError("Local development host accepts only loopback callers")
        return identity

    return authenticate


def main() -> None:
    """Start either guarded local development or isolated Foundry hosting."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("config/profiles/assistant.yaml"))
    parser.add_argument("--hosted", action="store_true")
    parser.add_argument("--tenant")
    parser.add_argument("--subject")
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--telemetry-exporter", choices=("console", "application-insights"))
    args = parser.parse_args()
    config = load_harness_config(args.profile, Path("config/schema/v1/harness.schema.json"))
    if args.telemetry_exporter:
        config = config.model_copy(
            update={
                "observability": ObservabilityConfig(
                    exporter=args.telemetry_exporter, payloadCapture=False
                )
            }
        )
    if args.hosted:
        if args.tenant is not None or args.subject is not None:
            parser.error("--tenant and --subject cannot be used with --hosted")
        authenticate: Authenticator = FoundryIngressIdentity()
        tenant = authenticate.tenant_scope
        host = "0.0.0.0"
    else:
        if not args.tenant or not args.subject:
            parser.error("local hosting requires --tenant and --subject")
        identity = IdentityScope(tenant_scope=args.tenant, subject_scope=args.subject)
        authenticate = create_local_authenticator(identity)
        tenant = identity.tenant_scope
        host = "127.0.0.1"

    tools = Registry[Tool]("tool")
    tools.register("get_service_status", ServiceStatusTool({tenant: {"api": "healthy"}}))
    credential = create_foundry_credential(hosted=args.hosted)

    async def serve() -> None:
        provider = None
        try:
            if config.observability.exporter == "application-insights":
                from agent_framework.observability import enable_instrumentation

                provider = create_telemetry_provider(os.environ)
                trace.set_tracer_provider(provider)
                enable_instrumentation(
                    enable_sensitive_data=False, enable_message_events=False, force=True
                )
            client = create_foundry_chat_client(config, credential=credential, hosted=args.hosted)
            try:
                app = create_responses_host(
                    config=config,
                    client=client,
                    tools=tools,
                    audit_sink=InMemoryAuditSink(Path("config/schema/v1/run-event.schema.json")),
                    authenticate=authenticate,
                    telemetry_provider=provider,
                )
                await app.run_async(host=host, port=args.port)
            finally:
                try:
                    await client.client.close()
                finally:
                    await client.project_client.close()
        finally:
            credential.close()
            if provider is not None:
                provider.shutdown()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
