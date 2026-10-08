# ADR 0005: Use Cosmos DB as the Reference Durable Run-State Store

- **Status:** Accepted
- **Date:** 8 October 2026

## Context

The action-agent path needs authoritative lifecycle state, optimistic concurrency, checkpoints,
approval proposals, resume tokens, and idempotency records. Conversation history is not a system of
record. Related state changes must remain scoped to one tenant, subject, and run.

## Decision

Use Azure Cosmos DB for NoSQL as the v1.0 Azure reference adapter for durable run state. Use an
in-memory adapter for deterministic local tests. Store run state, checkpoints, approval proposal
references, and idempotency records as distinct typed records under a hierarchical tenant, subject,
and run partition boundary. Use ETags for optimistic concurrency and transactional batches only
within one complete logical partition.

Store large immutable artifacts in Azure Blob Storage and retain only typed handles in run state.
Audit history remains a separate append-only concern.

## Consequences

- Retry and resume semantics can be tested against in-memory and Cosmos DB adapters.
- Cross-run or cross-tenant transactions are not part of the contract.
- Partition design, TTL, backup, and production consistency settings require deployment
  configuration.
- External side effects still require an idempotency key at the target boundary; a database record
  alone cannot make a non-idempotent external API atomic.
