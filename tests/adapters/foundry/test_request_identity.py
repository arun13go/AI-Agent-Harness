"""Prove Foundry identity mapping is bounded, explicit, and fail-closed."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from agent_harness.adapters.foundry.request_identity import FoundryIngressIdentity
from agent_harness.core.errors import AuthorizationError, ConfigurationError

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"


def environment() -> dict[str, str]:
    return {
        "FOUNDRY_HOSTING_ENVIRONMENT": "test-isolated-ingress",
        "FOUNDRY_AGENT_TENANT_ID": TENANT,
    }


def request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers})


def headers(user: bytes = b"opaque-user-one") -> list[tuple[bytes, bytes]]:
    return [(b"x-agent-user-id", user), (b"x-agent-foundry-call-id", b"opaque-call-one")]


async def test_platform_identity_is_stable_tenant_scoped_and_pseudonymous() -> None:
    auth = FoundryIngressIdentity(environment=environment())
    identity = await auth(request(headers()))
    assert identity == await auth(request(headers()))
    assert identity.tenant_scope == TENANT
    assert identity.subject_scope.startswith("foundry-")
    assert "opaque-user-one" not in repr(identity)
    assert identity != await auth(request(headers(b"opaque-user-two")))
    other_env = environment() | {"FOUNDRY_AGENT_TENANT_ID": OTHER_TENANT}
    other_identity = await FoundryIngressIdentity(environment=other_env)(request(headers()))
    assert identity.subject_scope != other_identity.subject_scope


@pytest.mark.parametrize(
    "invalid_headers",
    [
        [],
        [(b"x-agent-user-id", b"opaque-user-one")],
        [(b"x-agent-foundry-call-id", b"opaque-call-one")],
        headers(b""),
        [*headers(), (b"x-agent-user-id", b"opaque-user-two")],
        [*headers(), (b"x-agent-foundry-call-id", b"other-call")],
        [(b"x-agent-user-id", b"opaque-user-one"), (b"x-agent-foundry-call-id", b"")],
        [(b"x-agent-user-id", b"opaque-user-one"), (b"x-agent-foundry-call-id", b"x" * 513)],
        [(b"x-agent-user-id", b"opaque-user-one"), (b"x-agent-foundry-call-id", b"call\nvalue")],
        headers(b"x" * 513),
    ],
)
async def test_invalid_context_never_authenticates(
    invalid_headers: list[tuple[bytes, bytes]],
) -> None:
    with pytest.raises(AuthorizationError):
        await FoundryIngressIdentity(environment=environment())(request(invalid_headers))


async def test_client_identity_claims_cannot_override_deployment_binding() -> None:
    identity = await FoundryIngressIdentity(environment=environment())(
        request(
            [
                *headers(),
                (b"x-client-tenant", OTHER_TENANT.encode()),
                (b"x-client-subject", b"admin-subject"),
                (b"authorization", b"Bearer not-a-verified-token"),
            ]
        )
    )
    assert identity.tenant_scope == TENANT
    assert identity.subject_scope != "admin-subject"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FOUNDRY_HOSTING_ENVIRONMENT", ""),
        ("FOUNDRY_AGENT_TENANT_ID", ""),
        ("FOUNDRY_AGENT_TENANT_ID", "not-a-tenant"),
        ("FOUNDRY_AGENT_TENANT_ID", "00000000-0000-0000-0000-000000000000"),
        ("HARNESS_ALLOWED_FOUNDRY_USERS", ""),
        ("HARNESS_ALLOWED_FOUNDRY_USERS", '["opaque-user-one"]'),
    ],
    ids=[f"configuration-{index}" for index in range(6)],
)
def test_invalid_deployment_configuration_fails_without_echoing_allowlist(
    name: str, value: str
) -> None:
    with pytest.raises(ConfigurationError) as error:
        FoundryIngressIdentity(environment=environment() | {name: value})
    assert "opaque-user" not in str(error.value)
