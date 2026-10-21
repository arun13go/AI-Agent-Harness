# ADR 0013: Delegate Hosted Caller Authorization to Foundry Endpoint RBAC

- **Status:** Accepted; supersedes ADR 0011's application user allowlist
- **Date:** 21 October 2026

## Context

Foundry exposes an opaque user partition key on authenticated requests. The official sources
inspected do not document a pre-invocation key lookup, so the original named-key enrollment
design blocks deployment. The operator explicitly approved platform RBAC instead, including
existing inherited endpoint access.

## Decision

Foundry's authenticated ingress enforces effective endpoint RBAC. Use Foundry Agent Consumer
for new invoke-only assignments at project or individual-agent scope; no new assignments are
needed for the existing authorized development operator. Narrower assignments do not revoke
broader inherited Foundry User access. Do not claim a named-user allowlist.

The container still requires the platform hosting marker, a nonzero deployment tenant UUID,
and exactly one bounded printable user key and call ID per request. Hash the platform key
with the configured tenant to obtain a deterministic pseudonymous subject. Never trust
client-supplied tenant/subject claims, parse opaque keys, or infer a caller's home tenant.
Reject obsolete `HARNESS_ALLOWED_FOUNDRY_USERS` configuration rather than silently changing
an operator's intended policy.

Only Foundry ingress may reach the hosted listener. Headers and environment variables alone
are not cryptographic authentication. Preserve loopback-only local identity and all tool,
approval, audit, and execution controls. Delegated end-user impersonation is not configured.

## Acceptance

Local tests prove platform-context validation, subject isolation, claim resistance, and HTTP
composition. Deployment must separately prove authorized invocation, unauthenticated denial,
forged-header resistance, no ingress bypass, and actual runtime credential/model behavior.
An unauthorized Entra principal test needs an independently available test identity; do not
create credentials or grant broad roles just to manufacture evidence.

## References

- [Original ingress decision](0011-single-tenant-foundry-ingress-identity.md)
- [Foundry RBAC](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry)
- [Runtime contract](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-contract)