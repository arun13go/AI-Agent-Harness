# ADR 0010: Start HTTP Hosting with Stateless Governed Requests

- **Status:** Accepted
- **Date:** 20 October 2026

## Decision

Use released `azure-ai-agentserver-core` and `azure-ai-agentserver-responses` packages directly
around `MafAgentExecutor`. Do not use the preview MAF hosting convenience wrapper or construct
a second MAF agent outside the existing governance boundary.

The host factory requires a code-provided authenticator that returns a verified `IdentityScope`.
JSON input, model instructions, and caller-supplied identity headers cannot establish identity.
The initial executable binds only to loopback and uses explicit local development identity.
It is not an Azure authentication adapter and must not be exposed as a cloud entry point.

Initially support text-only, stateless `POST /responses` (JSON or SSE) and unauthenticated
`GET /readiness`. Default `store` to false and reject persistence, background runs, history,
client tools, client instructions, and non-user message roles before the SDK executes anything.
Limit input to 64 KiB and output to 4096 tokens, in addition to profile execution ceilings.

Explicitly supply an in-memory SDK response provider and disable SDK automatic observability
configuration. Its default file storage and sensitive-content instrumentation do not satisfy
the harness privacy policy. This does not disable existing MAF tracing or append-only audit.
Azure export must be deliberately composed with payload capture disabled in a later increment.

## Rationale

The released SDK owns wire-format events, SSE ordering, response lifecycle, and HTTP serving.
The harness owns authentication, accepted capabilities, source labels, tool governance, audit,
and normalized failures. Keeping those responsibilities separate avoids both a custom protocol
and an ungoverned convenience-wrapper path.

Stored conversation ownership and background-run recovery introduce persistence and authorization
requirements not yet implemented. Rejecting them is safer than exposing SDK defaults that could
retain payloads or serve another caller's response by identifier.

## Consequences

- Non-streaming, streaming, cancellation, failure mapping, and tool execution use the same executor.
- SDK cancellation and shutdown signals cancel the running model task and preserve cancellation audit.
- Local Inspector/debug configuration is provided, but live Inspector acceptance remains unproven.
- Inspector clients must use the supported text-only stateless request subset.
- Foundry Hosted Agents is the selected Azure target, not Container Apps.
- Cloud completion still requires verified platform identity mapping, canonical Bicep, generated
  portal deployment, managed identity, RBAC, safe Application Insights export, and live smoke evidence.

## References

- [ADR 0009](0009-defer-agent-inspector-to-http-host.md)
- [Responses SDK](https://pypi.org/project/azure-ai-agentserver-responses/)
- [Core hosting SDK](https://pypi.org/project/azure-ai-agentserver-core/)
- [Implementation plan](../delivery/implementation-plan.md)
