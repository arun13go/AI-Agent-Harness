# ADR 0002: Use Azure Dev as the v1.0 Development Environment

- **Status:** Accepted
- **Date:** 17 September 2026

## Decision

Use a customer-tenant Azure Dev environment as the supported v1.0 development and integration path. It hosts the Agent Harness/API, Foundry inference and evaluation, telemetry, retrieval, persistent state, cache and deployed tools through `azd up`.

Keep local use limited to source editing, deterministic tests, in-memory adapters and optional debugging against Azure Dev. Local model hosting is not part of v1.0.

## Consequences

- Development exercises production-like identity, networking and service integrations early.
- Local policy and contract tests remain fast and cloud-independent.
- Integration development requires Azure access and incurs controlled development cost.
- Production remains a separate environment with stronger networking, scale and retention controls.