"""Prove the typed release preserves the reference azd and Foundry wire contracts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agent_harness.adapters.foundry.deployment import FoundryDeploymentTarget, HostedAgentRelease
from agent_harness.core.errors import ConfigurationError

IMAGE = "example.azurecr.io/agent-harness/release@sha256:" + "a" * 64
MODEL = "example-model"
PROJECT_ID = (
    "/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/example-rg"
    "/providers/Microsoft.CognitiveServices/accounts/example-account/projects/example-project"
)
ENDPOINT = "https://example-account.services.ai.azure.com/api/projects/example-project"
ROOT = Path(__file__).parents[2]


def test_exact_rest_definition_and_manifest_equivalence() -> None:
    release = HostedAgentRelease(image=IMAGE, model_deployment_name=MODEL)
    service = yaml.safe_load((ROOT / "azure.yaml").read_text())["services"]["harness"]
    release.validate_azd_service(service, {"AZURE_AI_MODEL_DEPLOYMENT_NAME": MODEL})
    assert release.rest_definition() == {
        "kind": "hosted",
        "container_configuration": {"image": IMAGE},
        "cpu": "1",
        "memory": "2Gi",
        "protocol_versions": [{"protocol": "responses", "version": "2.0.0"}],
        "environment_variables": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": MODEL},
    }
    expected = release.azd_service_settings()
    assert {key: service[key] for key in expected} == expected
    assert release.name == service["name"]


@pytest.mark.parametrize(
    "image",
    [
        "example.azurecr.io/agent-harness:latest",
        "example.azurecr.io/agent-harness:release@sha256:" + "a" * 64,
        "https://" + IMAGE,
        "user:password@" + IMAGE,
        IMAGE + "?token=value",
        IMAGE.replace("azurecr.io", "untrusted.example"),
        IMAGE[:-1],
        IMAGE.upper(),
    ],
)
def test_rejects_mutable_or_unreviewed_image_references(image: str) -> None:
    with pytest.raises(ValidationError):
        HostedAgentRelease(image=image, model_deployment_name=MODEL)


@pytest.mark.parametrize(
    "override",
    [
        {"name": "another-agent"},
        {"cpu": "2"},
        {"memory": "4Gi"},
        {"instructions": "override"},
        {"environment_variables": {"FOUNDRY_AGENT_TENANT_ID": "override"}},
        {"environment_variables": {"APPLICATIONINSIGHTS_CONNECTION_STRING": "override"}},
        {"model_deployment_name": "model\ninjected"},
        {"model_deployment_name": ""},
    ],
)
def test_rejects_unshipped_release_settings(override: dict[str, object]) -> None:
    values: dict[str, object] = {"image": IMAGE, "model_deployment_name": MODEL}
    values.update(override)
    with pytest.raises(ValidationError):
        HostedAgentRelease.model_validate(values)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "another-agent"),
        ("protocols", [{"protocol": "responses", "version": "1.0.0"}]),
        ("container", {"resources": {"cpu": "2", "memory": "4Gi"}}),
        ("environmentVariables", [{"name": "FOUNDRY_AGENT_TENANT_ID", "value": "override"}]),
        ("environmentVariables", [{"name": "AGENT_NAME", "value": "override"}]),
        (
            "environmentVariables",
            [{"name": "APPLICATIONINSIGHTS_CONNECTION_STRING", "value": "override"}],
        ),
        ("codeConfiguration", {"runtime": "python_3_13"}),
        ("image", IMAGE),
        ("startupCommand", "unreviewed-command"),
        ("docker", {"remoteBuild": False}),
        ("project", "other"),
    ],
)
def test_rejects_manifest_drift(key: str, value: object) -> None:
    service = deepcopy(yaml.safe_load((ROOT / "azure.yaml").read_text())["services"]["harness"])
    service[key] = value
    release = HostedAgentRelease(image=IMAGE, model_deployment_name=MODEL)
    with pytest.raises(ConfigurationError):
        release.validate_azd_service(service, {"AZURE_AI_MODEL_DEPLOYMENT_NAME": MODEL})


@pytest.mark.parametrize("environment", [{}, {"AZURE_AI_MODEL_DEPLOYMENT_NAME": "other"}])
def test_rejects_missing_or_different_resolved_model(environment: dict[str, str]) -> None:
    service = yaml.safe_load((ROOT / "azure.yaml").read_text())["services"]["harness"]
    release = HostedAgentRelease(image=IMAGE, model_deployment_name=MODEL)
    with pytest.raises(ConfigurationError, match="model selections differ"):
        release.validate_azd_service(service, environment)


def test_accepts_bound_public_azure_target() -> None:
    target = FoundryDeploymentTarget(project_resource_id=PROJECT_ID, project_endpoint=ENDPOINT)
    assert target.project_endpoint == ENDPOINT
    assert target.project_resource_id == PROJECT_ID


@pytest.mark.parametrize(
    "endpoint",
    [
        ENDPOINT.replace("example-account", "other-account"),
        ENDPOINT.replace("example-project", "other-project"),
        ENDPOINT.replace("https:", "http:"),
        ENDPOINT.replace(".azure.com", ".azure.com.attacker.example"),
        ENDPOINT.replace("https://", "https://user:password@"),
        ENDPOINT + "?api-version=v1",
        ENDPOINT + "#fragment",
        ENDPOINT + "/",
        ENDPOINT.replace("/api/", ":443/api/"),
    ],
)
def test_rejects_mismatched_or_ambiguous_target(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        FoundryDeploymentTarget(project_resource_id=PROJECT_ID, project_endpoint=endpoint)


@pytest.mark.parametrize(
    "resource_id",
    [
        PROJECT_ID + "/agents/agent-harness",
        PROJECT_ID.replace("11111111-1111-4111-8111-111111111111", "0" * 36),
        PROJECT_ID.replace(
            "11111111-1111-4111-8111-111111111111", "00000000-0000-0000-0000-000000000000"
        ),
        PROJECT_ID.replace("Microsoft.CognitiveServices", "Microsoft.Other"),
    ],
)
def test_rejects_invalid_project_identity(resource_id: str) -> None:
    with pytest.raises(ValidationError):
        FoundryDeploymentTarget(project_resource_id=resource_id, project_endpoint=ENDPOINT)


def test_release_outputs_are_detached_from_immutable_input() -> None:
    release = HostedAgentRelease(image=IMAGE, model_deployment_name=MODEL)
    definition = release.rest_definition()
    definition["kind"] = "prompt"
    assert release.rest_definition()["kind"] == "hosted"
    with pytest.raises(ValidationError):
        release.image = IMAGE
