# ADR 0012: Package the Python 3.12 Harness for Foundry Hosted Agents

- **Status:** Accepted
- **Date:** 20 October 2026

## Decision

Use container deployment to Microsoft Foundry Hosted Agents, as explicitly selected by the
operator. This is not an Azure Container Apps or AKS deployment. Retain the tested Python 3.12
support range and the pinned uv dependency lock.

The root Dockerfile installs only production dependencies with frozen resolution, installs the
application non-editably, runs as a non-root user, exposes port 8088, and selects the guarded
`--hosted` entry point. An allowlist-only Docker build context excludes local logs, credentials,
Azure state, tests, and development environments.

The hosted model client uses released MAF chat middleware to forward the current SDK platform
call ID through per-call headers. It never mutates shared client headers or forwards the raw user
key. Missing or invalid context fails before a model request. Local model clients do not forward
platform headers. Hosted credentials exclude client secrets and developer login fallbacks,
retaining platform managed/workload identity discovery.

## Consequences

- Container images require a registry and an image build. Prefer ACR remote build so local Docker
  is optional. Registry selection and its Foundry connection remain deployment prerequisites.
- The deployment must explicitly declare the Responses 2.0 protocol and preserve authenticated
  Foundry ingress. Do not publish this container as a standalone public HTTP service.
- The platform supplies its hosting marker and tenant; the operator supplies the opaque user-key
  allowlist. Do not bake either tenant or user identifiers into the image.
- The existing project and model are reused through local deployment settings, not committed
  customer identifiers. Do not reprovision or delete the shared project through this template.
- Canonical Bicep, its generated portal artifact, safe cloud trace export, live agent credentials,
  and live ingress tests remain required before declaring Phase 1D complete.
- Local HTTP and transport tests do not prove an image can build, run in Foundry, or resist
  platform-header spoofing. Record those results separately after an actual build and deployment.

## Increment Evidence

The dedicated registry and connection were provisioned through canonical Bicep using azd.
ACR remote build and a separate digest-pinned image smoke task succeeded on 20 October 2026.
The smoke task verified non-root execution, local readiness, input denial, and hosted startup
refusal without platform context. Foundry-hosted authentication and telemetry remain unproven.

## References

- [Ingress identity decision](0011-single-tenant-foundry-ingress-identity.md)
- [Hosted agent concepts](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Developer code tour](../getting-started/code-tour.md#phase-1d-http-path)
