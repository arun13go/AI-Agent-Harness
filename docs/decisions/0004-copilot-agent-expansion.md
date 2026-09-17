# ADR 0004: Defer Copilot Agent Expansion to v2/v3

- **Status:** Accepted
- **Date:** 17 September 2026

## Decision

Version 1.0 remains the Python MAF Harness and Microsoft Foundry reference implementation. Copilot agent support is deferred to a separate v2/v3 expansion track.

Microsoft 365 Copilot agents, the Microsoft 365 Agents SDK, Copilot Studio agents, and GitHub Copilot agents are distinct integration surfaces. The harness will not expose a generic `runtime: copilot` setting or claim that these products are interchangeable with the MAF runtime.

Version 2 may select one Microsoft 365 Copilot scenario for a read-only or propose-only prototype. The prototype must classify the integration as a runtime adapter, channel adapter, tool integration, or deployment profile and test identity, authorization, state, tools, approvals, audit, telemetry, and evaluation against the shared contracts.

Version 3 may productize an integration only after the prototype passes the applicable conformance tests and field demand justifies its maintenance. Product-native governance remains authoritative wherever it is stricter than harness policy.

## Consequences

- Copilot expansion does not delay or widen the v1.0 critical path.
- Each Copilot surface receives an explicit architecture and lifecycle assessment rather than configuration-only substitution.
- Shared governance and evaluation assets are reused only where their guarantees can be demonstrated end to end.
- Consequential actions remain unavailable until approval, idempotency, audit, and identity semantics reach contract parity.
- GitHub Copilot coding agents remain a separate scenario track from Microsoft 365 business agents.

## References

- [Extend Microsoft 365 Copilot](https://learn.microsoft.com/microsoft-365-copilot/extensibility/overview)
- [Microsoft 365 Agents SDK](https://learn.microsoft.com/microsoft-365/agents-sdk/)
- [Architecting agent solutions for Microsoft 365 Copilot](https://learn.microsoft.com/agents/architecture/)
- [About GitHub Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent)