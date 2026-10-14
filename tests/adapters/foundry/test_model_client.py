"""Verify Foundry model configuration and bounded client composition."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from azure.core.credentials import AccessToken, TokenCredential

from agent_harness.adapters.foundry.model_client import (
    create_foundry_chat_client,
    resolve_foundry_connection_settings,
)
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.errors import ConfigurationError

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
            "FOUNDRY_PROJECT_ENDPOINT": (
                "https://example.services.ai.azure.com/api/projects/test"
            ),
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
