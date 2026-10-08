# ADR 0006: Use Cosmos DB as the Reference Memory Store

- **Status:** Accepted
- **Date:** 8 October 2026

## Context

Conversation and episodic memory require tenant and subject isolation, provenance, retention,
deletion, and explicit write policy. Memory is not durable workflow state and must not silently turn
model output into trusted fact.

## Decision

Use Azure Cosmos DB for NoSQL as the v1.0 Azure reference memory adapter, with separate containers
or schemas from run state and audit. Use the MAF memory/provider integration where it preserves the
shared contract. Use an in-memory adapter for local deterministic tests.

Every memory record carries tenant scope, subject scope, kind, provenance, sensitivity, policy
version, creation time, and expiry. Conversation memory has a bounded TTL. Episodic writes require a
code-owned policy decision. Semantic and preference memory remain contract-only for v1.0.

## Consequences

- One Azure service can support separately governed state and memory containers without conflating
  their contracts.
- Deletion and expiry are first-class conformance requirements.
- Retrieved memory is always labeled untrusted before entering model context.
- Customer production overlays may select another adapter only after it passes the same suite.
