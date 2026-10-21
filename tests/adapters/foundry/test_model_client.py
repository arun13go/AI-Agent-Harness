"""Verify Foundry model configuration and bounded client composition."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2 as httpx
import pytest
from agent_framework import Message
from azure.ai.agentserver.core import (
    FoundryAgentRequestContext,
    reset_request_context,
    set_request_context,
)
from azure.core.credentials import AccessToken, TokenCredential

from agent_harness.adapters.foundry.model_client import (
    create_foundry_chat_client,
    create_foundry_credential,
    resolve_foundry_connection_settings,
)
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.errors import ConfigurationError, ModelAuthenticationError

ROOT = Path(__file__).parents[3]
CONFIG_SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"


class StaticCredential(TokenCredential):
    """Provide a non-network token credential for client construction."""

    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken:
        del scopes, claims, tenant_id, enable_cae, kwargs
        return AccessToken("test-token", int(datetime(2100, 1, 1, tzinfo=UTC).timestamp()))


def load_config():
    return load_harness_config(
        ROOT / "config" / "profiles" / "assistant.yaml",
        CONFIG_SCHEMA,
    )


def response_body() -> dict[str, object]:
    return {
        "id": "resp-test",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "id": "msg-test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "healthy", "annotations": []}],
            }
        ],
    }


def test_missing_foundry_environment_is_explicit() -> None:
    with pytest.raises(
        ConfigurationError,
        match="FOUNDRY_PROJECT_ENDPOINT, AZURE_AI_MODEL_DEPLOYMENT_NAME",
    ):
        resolve_foundry_connection_settings(load_config(), {})


def test_foundry_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ConfigurationError, match="without credentials"):
        resolve_foundry_connection_settings(
            load_config(),
            {
                "FOUNDRY_PROJECT_ENDPOINT": "https://user@example.invalid/api/projects/test",
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-model",
            },
        )


@pytest.mark.asyncio
async def test_foundry_client_enforces_runtime_ceilings() -> None:
    config = load_config()
    client = create_foundry_chat_client(
        config,
        environment={
            "FOUNDRY_PROJECT_ENDPOINT": ("https://example.services.ai.azure.com/api/projects/test"),
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-model",
        },
        credential=StaticCredential(),
    )
    try:
        assert client.model == "test-model"
        invocation = client.function_invocation_configuration
        assert invocation.get("max_iterations") == 12
        assert invocation.get("max_function_calls") == 4
        assert invocation.get("max_duration_seconds") == 120.0
        assert invocation.get("terminate_on_unknown_calls") is True
        assert invocation.get("include_detailed_errors") is False
        assert invocation.get("allow_concurrent_invocation") is False
    finally:
        await client.project_client.close()


@pytest.mark.parametrize("stream", [False, True], ids=["json", "stream"])
async def test_hosted_call_context_reaches_wire_without_cross_request_leakage(stream: bool) -> None:
    seen: list[tuple[str | None, str | None]] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        seen.append(
            (
                request.headers.get("x-agent-foundry-call-id"),
                request.headers.get("x-agent-user-id"),
            )
        )
        assert request.headers["authorization"].startswith("Bearer ")
        body = response_body()
        if stream:
            events = [
                {"type": "response.created", "sequence_number": 0, "response": body},
                {"type": "response.completed", "sequence_number": 1, "response": body},
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="".join(f"data: {json.dumps(event)}\n\n" for event in events),
            )
        return httpx.Response(200, json=body)

    client = create_foundry_chat_client(
        load_config(),
        environment={
            "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-model",
        },
        credential=StaticCredential(),
        hosted=True,
    )
    original = client.client
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client.client = original.with_options(http_client=http, max_retries=0)

        async def invoke(call_id: str) -> None:
            token = set_request_context(
                FoundryAgentRequestContext(call_id=call_id, user_id=f"user-{call_id}")
            )
            try:
                if stream:
                    await client.get_response(
                        [Message("user", "Hi")], stream=True
                    ).get_final_response()
                else:
                    await client.get_response([Message("user", "Hi")])
            finally:
                reset_request_context(token)

        try:
            await asyncio.gather(invoke("call-one"), invoke("call-two"))
            assert sorted(seen) == [("call-one", None), ("call-two", None)]
            for call_id in (None, "", "bad\ncall", "x" * 513):
                token = set_request_context(FoundryAgentRequestContext(call_id=call_id))
                try:
                    with pytest.raises(
                        ModelAuthenticationError,
                        match="Hosted model calls require Foundry request context",
                    ):
                        await client.get_response([Message("user", "Hi")])
                finally:
                    reset_request_context(token)
            assert len(seen) == 2
        finally:
            await client.client.close()
            await original.close()
            await client.project_client.close()


def test_hosted_credentials_exclude_secrets_and_developer_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_TOKEN_CREDENTIALS", raising=False)
    with create_foundry_credential(hosted=True) as credential:
        providers = {type(provider).__name__ for provider in credential.credentials}
        assert providers <= {
            "WorkloadIdentityCredential",
            "ManagedIdentityCredential",
            "FailedDACCredential",
        }
        assert "ManagedIdentityCredential" in providers


async def test_local_model_client_does_not_forward_platform_headers() -> None:
    seen: list[httpx.Headers] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json=response_body())

    client = create_foundry_chat_client(
        load_config(),
        environment={
            "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/test",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-model",
        },
        credential=StaticCredential(),
    )
    original = client.client
    token = set_request_context(
        FoundryAgentRequestContext(call_id="untrusted-local-call", user_id="untrusted-user")
    )
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client.client = original.with_options(http_client=http)
            await client.get_response([Message("user", "Hi")])
            assert len(seen) == 1
            assert "x-agent-foundry-call-id" not in seen[0]
            assert "x-agent-user-id" not in seen[0]
            await client.client.close()
    finally:
        reset_request_context(token)
        await original.close()
        await client.project_client.close()
