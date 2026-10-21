# ADR 0011: Bind Foundry Ingress to One Tenant and an Explicit User Allowlist

- **Status:** Superseded for caller authorization by [ADR 0013](0013-foundry-rbac-ingress-authorization.md); tenant/subject mapping retained
- **Date:** 20 October 2026

## Decision

The initial Azure Dev deployment serves one configured customer tenant. Read its tenant from
the platform's `FOUNDRY_AGENT_TENANT_ID`, require a nonzero UUID, and require an operator-supplied
`HARNESS_ALLOWED_FOUNDRY_USERS` JSON array of opaque Foundry user keys. An empty, invalid, oversized,
or duplicate allowlist fails startup; there is no allow-all option.

Only a Foundry-isolated deployment may compose `FoundryIngressIdentity`. Require exactly one
nonempty, bounded `x-agent-user-id` and `x-agent-foundry-call-id`. Authorize only allowlisted user
keys. Map each user key to `foundry-` plus SHA-256 of the tenant UUID, a NUL separator, and that key.
This produces a deterministic tenant-bound pseudonymous harness subject. It does not anonymize
the caller or replace authorization.

Never derive tenant scope from JSON, forwarded client headers, or an unverified bearer token.
Never record raw user keys or call identifiers in harness audit events or rejection messages.
The local authenticator ignores platform headers and refuses composition in a hosted environment.

## Trust Boundary

Foundry authenticates the external caller and supplies the opaque platform context through its
isolated ingress. The adapter relies on that deployment guarantee: it does not validate a JWT,
resolve an Entra object ID, or cryptographically verify HTTP headers. A hosting environment marker,
allowlisted key, and call ID are not independently proof of authenticity.

Do not expose this adapter on arbitrary public HTTP, behind a proxy that preserves caller-forged
platform headers, or on a local server. Deployment acceptance must prove that clients cannot
bypass Foundry ingress or overwrite its identity headers. Platform user keys are not assumed to
be Entra object IDs; allowlist enrollment must use an authorized platform source.

## Consequences

- The hosted factory reuses the existing strict HTTP boundary, MAF executor, tool registry, and audit.
- User-specific scope survives the tool and model lifecycle; one deployment cannot select another
  tenant through request content.
- Multi-customer tenancy needs an explicit verified resolver and is not implied by this adapter.
- Local tests simulate the ingress contract; they do not prove live platform header authenticity.
- Guarded hosted composition and request-scoped call-context forwarding are now implemented.
  Live agent identity credentials and cloud ingress evidence remain unproven.
- Forward `x-agent-foundry-call-id` verbatim on outbound Foundry platform calls as required by the
  container protocol. Do not forward `x-agent-user-id` as downstream authentication.
- Canonical Bicep, portal deployment, RBAC, telemetry export, and cloud smoke tests remain open.

## References

- [ADR 0010](0010-stateless-governed-responses-host.md)
- [Core SDK request context](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/agentserver/azure-ai-agentserver-core/azure/ai/agentserver/core/_request_context.py)
- [Responses SDK platform context](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/agentserver/azure-ai-agentserver-responses/azure/ai/agentserver/responses/_response_context.py)
- [Hosted agent architecture](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
