# pyright: reportMissingTypeStubs=false

"""Create the released MAF Foundry model client from validated environment references."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent_framework import ChatContext, ChatMiddleware
from agent_framework_foundry import (
    FoundryChatClient,
    FoundryChatOptions,
)
from azure.ai.agentserver.core import get_request_context
from azure.core.credentials import TokenCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity import DefaultAzureCredential

from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.errors import ConfigurationError, ModelAuthenticationError


class _FoundryCallContext(ChatMiddleware):
    """Forward only the current platform call ID on hosted model requests."""

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        platform = get_request_context()
        if (
            not platform.call_id
            or len(platform.call_id) > 512
            or any(not 33 <= ord(char) <= 126 for char in platform.call_id)
        ):
            raise ModelAuthenticationError("Hosted model calls require Foundry request context")
        context.kwargs["extra_headers"] = platform.platform_headers()
        await call_next()


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


def create_foundry_credential(*, hosted: bool = False) -> DefaultAzureCredential:
    """Use passwordless credentials, excluding developer identities when hosted."""

    if not hosted:
        return DefaultAzureCredential()
    return DefaultAzureCredential(
        exclude_environment_credential=True,
        exclude_cli_credential=True,
        exclude_powershell_credential=True,
        exclude_developer_cli_credential=True,
        exclude_visual_studio_code_credential=True,
        exclude_shared_token_cache_credential=True,
        exclude_interactive_browser_credential=True,
        exclude_broker_credential=True,
    )


def create_foundry_chat_client(
    config: HarnessConfig,
    *,
    environment: Mapping[str, str] | None = None,
    credential: TokenCredential | AsyncTokenCredential | None = None,
    hosted: bool = False,
) -> FoundryChatClient[FoundryChatOptions]:
    """Create a passwordless Foundry client with code-owned execution ceilings."""

    settings = resolve_foundry_connection_settings(config, environment)
    effective_credential = credential or create_foundry_credential(hosted=hosted)
    return FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.deployment_name,
        credential=effective_credential,
        middleware=[_FoundryCallContext()] if hosted else None,
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
