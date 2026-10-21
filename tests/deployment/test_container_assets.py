"""Check the declared container contract and registry-only deployment boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_manifest_selects_foundry_container_protocol_and_runtime_environment() -> None:
    manifest = yaml.safe_load((ROOT / "azure.yaml").read_text())
    assert manifest["infra"]["provider"] == "bicep"
    assert set(manifest["services"]) == {"harness"}
    service = manifest["services"]["harness"]
    assert service["host"] == "azure.ai.agent"
    assert service["kind"] == "hosted"
    assert service["language"] == "docker"
    assert "codeConfiguration" not in service
    assert service["docker"] == {
        "path": "Dockerfile",
        "context": ".",
        "remoteBuild": True,
    }
    assert service["protocols"] == [{"protocol": "responses", "version": "2.0.0"}]
    variables = {item["name"]: item["value"] for item in service["environmentVariables"]}
    assert "HARNESS_ALLOWED_FOUNDRY_USERS" not in variables
    assert variables["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "${AZURE_AI_MODEL_DEPLOYMENT_NAME}"
    assert "FOUNDRY_PROJECT_ENDPOINT" not in variables
    assert "APPLICATIONINSIGHTS_CONNECTION_STRING" not in variables
    assert not any(name.startswith(("FOUNDRY_", "AGENT_")) for name in variables)
    assert "FOUNDRY_HOSTING_ENVIRONMENT" not in variables
    assert "FOUNDRY_AGENT_TENANT_ID" not in variables


def test_container_defaults_to_guarded_nonroot_production_runtime() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "python:3.12.13-slim-bookworm" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.11.15" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert 'CMD ["--hosted", "--telemetry-exporter", "application-insights"]' in dockerfile
    assert (
        'ENTRYPOINT ["/app/.venv/bin/python", "-m", "agent_harness.app.agent_http_server"]'
        in dockerfile
    )
    assert "EXPOSE 8088" in dockerfile
    assert "/readiness" in dockerfile
    assert "COPY . ." not in dockerfile


def test_generated_template_preserves_existing_project_and_limits_new_resources() -> None:
    template = json.loads((ROOT / "infra/main.json").read_text())
    assert template["$schema"].endswith("subscriptionDeploymentTemplate.json#")
    assert set(template["parameters"]) == {
        "environmentName",
        "location",
        "resourceGroupName",
        "foundryAccountName",
        "foundryProjectName",
        "modelDeploymentName",
    }

    assert set(template["resources"]) == {"existingGroup", "registry"}
    assert template["resources"]["existingGroup"]["existing"] is True
    resources = template["resources"]["registry"]["properties"]["template"]["resources"]
    assert set(resources) == {"account", "project", "connection", "registry"}
    assert resources["account"]["existing"] is True
    assert resources["project"]["existing"] is True
    assert resources["connection"]["properties"]["authType"] == "AAD"
    parameters = resources["registry"]["properties"]["parameters"]
    assert parameters["acrSku"]["value"] == "Basic"
    assert parameters["acrAdminUserEnabled"]["value"] is False
    assert parameters["anonymousPullEnabled"]["value"] is False
    assert parameters["roleAssignments"]["value"] == [
        {
            "principalId": "[reference('project', '2025-06-01', 'full').identity.principalId]",
            "principalType": "ServicePrincipal",
            "roleDefinitionIdOrName": "AcrPull",
        }
    ]
    assert template["outputs"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"]["value"] == (
        "[parameters('modelDeploymentName')]"
    )
