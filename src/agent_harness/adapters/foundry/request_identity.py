"""Map isolated Foundry ingress identity into tenant-scoped harness subjects."""

from __future__ import annotations

import os
from collections.abc import Mapping
from hashlib import sha256
from uuid import UUID

from starlette.requests import Request

from agent_harness.core.errors import AuthorizationError, ConfigurationError
from agent_harness.core.types import IdentityScope


class FoundryIngressIdentity:
    """Bind callers authorized by Foundry endpoint RBAC, never by client claims."""

    def __init__(self, *, environment: Mapping[str, str] | None = None) -> None:
        values = os.environ if environment is None else environment
        if not values.get("FOUNDRY_HOSTING_ENVIRONMENT", "").strip():
            raise ConfigurationError("Foundry ingress identity requires a hosted deployment")
        try:
            tenant = UUID(values.get("FOUNDRY_AGENT_TENANT_ID", ""))
        except ValueError:
            raise ConfigurationError("Foundry hosting requires a valid agent tenant ID") from None
        if tenant.int == 0:
            raise ConfigurationError("Foundry hosting requires a nonzero agent tenant ID")
        if "HARNESS_ALLOWED_FOUNDRY_USERS" in values:
            raise ConfigurationError(
                "Remove obsolete HARNESS_ALLOWED_FOUNDRY_USERS: "
                "caller authorization uses Foundry RBAC"
            )
        self.tenant_scope = str(tenant)

    def _subject(self, user_key: str) -> str:
        digest = sha256(f"{self.tenant_scope}\0{user_key}".encode()).hexdigest()
        return f"foundry-{digest}"

    async def __call__(self, request: Request) -> IdentityScope:
        """Bind platform user keys; never infer tenant or subject from client claims."""

        users = request.headers.getlist("x-agent-user-id")
        calls = request.headers.getlist("x-agent-foundry-call-id")
        if (
            len(users) != 1
            or len(calls) != 1
            or not users[0]
            or not calls[0]
            or len(users[0]) > 512
            or len(calls[0]) > 512
            or any(not 33 <= ord(char) <= 126 for char in users[0] + calls[0])
        ):
            raise AuthorizationError("Required Foundry platform identity context is invalid")
        return IdentityScope(tenant_scope=self.tenant_scope, subject_scope=self._subject(users[0]))
