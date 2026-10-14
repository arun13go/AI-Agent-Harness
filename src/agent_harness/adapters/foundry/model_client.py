# pyright: reportMissingTypeStubs=false

"""Create the released MAF Foundry model client from validated environment references."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_framework_foundry import (
    FoundryChatClient,
    FoundryChatOptions,
)
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import DefaultAzureCredential

from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class FoundryConnectionSettings:
    """Hold non-secret Foundry values resolved from the process environment."""

    project_endpoint: str
    deployment_name: str


def resolve_foundry_connection_settings(
    config: HarnessConfig,
    environment: Mapping[str, str] | None = None,
) -> FoundryConnectionSettings:
    """Resolve and validate Foundry endpoint and deployment environment references."""

    values = os.environ if environment is None else environment
    endpoint_name = config.model.project_endpoint_env
    deployment_name = config.model.deployment_name_env
    endpoint = values.get(endpoint_name, "").strip()
    deployment = values.get(deployment_name, "").strip()
    missing = [
        name
        for name, value in (
            (endpoint_name, endpoint),
            (deployment_name, deployment),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            "Missing required Foundry environment variables: " + ", ".join(missing)
        )

    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            f"Foundry project endpoint from '{endpoint_name}' must be an HTTPS URL "
            "without credentials, query parameters, or fragments"
        )
    return FoundryConnectionSettings(
        project_endpoint=endpoint,
        deployment_name=deployment,
    )


def create_foundry_chat_client(
    config: HarnessConfig,
    *,
    environment: Mapping[str, str] | None = None,
    credential: TokenCredential | AsyncTokenCredential | None = None,
) -> FoundryChatClient[FoundryChatOptions]:
    """Create a passwordless Foundry client with code-owned execution ceilings."""

    settings = resolve_foundry_connection_settings(config, environment)
    effective_credential = credential or DefaultAzureCredential()
    return FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.deployment_name,
        credential=effective_credential,
        function_invocation_configuration={
            "enabled": True,
            "max_iterations": config.runtime.max_steps,
            "max_function_calls": config.runtime.max_tool_calls,
            "max_duration_seconds": float(config.runtime.timeout_seconds),
            "max_consecutive_errors_per_request": 0,
            "terminate_on_unknown_calls": True,
            "include_detailed_errors": False,
            "allow_concurrent_invocation": False,
        },
    )
