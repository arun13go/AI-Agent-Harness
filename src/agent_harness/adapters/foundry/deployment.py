"""Validate the reference hosted release and render equivalent deployment settings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_harness.core.errors import ConfigurationError
from agent_harness.core.types import JsonValue

_MODEL_VARIABLE = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
_PROJECT_ID = re.compile(
    r"/subscriptions/(?P<subscription>[0-9a-fA-F-]{36})"
    r"/resourceGroups/[a-zA-Z0-9_.()-]{1,90}"
    r"/providers/Microsoft\.CognitiveServices"
    r"/accounts/(?P<account>[a-zA-Z0-9][a-zA-Z0-9-]{1,62})"
    r"/projects/(?P<project>[a-zA-Z0-9][a-zA-Z0-9_-]{0,63})",
    re.IGNORECASE,
)


class FoundryDeploymentTarget(BaseModel):
    """Bind a public-Azure project endpoint to its declared ARM resource identity."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    project_resource_id: str
    project_endpoint: str

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        match = _PROJECT_ID.fullmatch(self.project_resource_id)
        if match is None:
            raise ValueError("Expected a Foundry project ARM resource ID")
        try:
            subscription = UUID(match["subscription"])
        except ValueError as error:
            raise ValueError("Expected a valid subscription UUID") from error
        if subscription.int == 0:
            raise ValueError("Subscription UUID must not be zero")
        expected = (
            f"https://{match['account'].lower()}.services.ai.azure.com"
            f"/api/projects/{match['project']}"
        )
        if self.project_endpoint != expected:
            raise ValueError("Project endpoint must match the declared public-Azure project")
        return self


class HostedAgentRelease(BaseModel):
    """Describe only the shipped harness configuration, not arbitrary agent settings."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: Literal["agent-harness"] = "agent-harness"
    image: str = Field(
        pattern=(
            r"^[a-z0-9]+(?:[.-][a-z0-9]+)*\.azurecr\.io/"
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
            r"@sha256:[0-9a-f]{64}$"
        ),
        max_length=512,
    )
    model_deployment_name: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$"
    )
    cpu: Literal["1"] = "1"
    memory: Literal["2Gi"] = "2Gi"

    def rest_definition(self) -> dict[str, JsonValue]:
        """Render the released Foundry hosted-definition wire shape."""

        return {
            "kind": "hosted",
            "container_configuration": {"image": self.image},
            "cpu": self.cpu,
            "memory": self.memory,
            "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
            "environment_variables": {_MODEL_VARIABLE: self.model_deployment_name},
        }

    def azd_service_settings(self) -> dict[str, JsonValue]:
        """Render execution settings with the existing azd model-variable reference."""

        return {
            "name": self.name,
            "host": "azure.ai.agent",
            "kind": "hosted",
            "language": "docker",
            "container": {"resources": {"cpu": self.cpu, "memory": self.memory}},
            "protocols": [{"protocol": "responses", "version": "2.0.0"}],
            "environmentVariables": [
                {"name": _MODEL_VARIABLE, "value": "${" + _MODEL_VARIABLE + "}"}
            ],
        }

    def validate_azd_service(
        self, service: Mapping[str, object], environment: Mapping[str, str]
    ) -> None:
        """Reject manifest drift and a model selection inconsistent with this release."""

        expected = self.azd_service_settings()
        allowed = set(expected) | {"project", "description", "docker"}
        if set(service) - allowed:
            raise ConfigurationError("Unsupported hosted azd service settings")
        for key, value in expected.items():
            if service.get(key) != value:
                raise ConfigurationError(f"Hosted azd service differs at '{key}'")
        if service.get("project") != "." or service.get("docker") != {
            "path": "Dockerfile",
            "context": ".",
            "remoteBuild": True,
        }:
            raise ConfigurationError("Hosted azd service must use the reviewed container build")
        if environment.get(_MODEL_VARIABLE) != self.model_deployment_name:
            raise ConfigurationError("Hosted release and azd model selections differ")
