"""Provide a deterministic tenant-scoped read tool for local runs."""

from __future__ import annotations

from collections.abc import Mapping

from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import (
    RequestContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolRiskTier,
)


class ServiceStatusTool:
    """Read configured service status within the caller's tenant."""

    def __init__(self, statuses: Mapping[str, Mapping[str, str]]) -> None:
        self._statuses = {
            tenant: dict(tenant_statuses) for tenant, tenant_statuses in statuses.items()
        }

    @property
    def definition(self) -> ToolDefinition:
        """Describe the tool's input and read-only risk tier."""

        return ToolDefinition(
            name="get_service_status",
            description="Read the current status of a service in the authenticated tenant",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["service"],
                "properties": {
                    "service": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                        "pattern": "^[a-z][a-z0-9_-]*$",
                    }
                },
            },
            risk_tier=ToolRiskTier.READ,
        )

    async def invoke(
        self,
        *,
        context: RequestContext,
        call: ToolCall,
        idempotency_key: str | None,
    ) -> ToolResult:
        """Return a tenant-scoped service status as untrusted output."""

        service = call.arguments.get("service")
        if not isinstance(service, str):
            raise InputValidationError("Service status input requires a string 'service'")

        status = self._statuses.get(context.identity.tenant_scope, {}).get(service)
        if status is None:
            raise InputValidationError("Service is not available in the authenticated tenant")

        return ToolResult(
            call_id=call.call_id,
            value={"service": service, "status": status},
        )
