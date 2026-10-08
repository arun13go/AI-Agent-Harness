# ADR 0007: Use Azure AI Search as the Reference Retrieval Adapter

- **Status:** Accepted
- **Date:** 8 October 2026

## Context

The grounded-assistant profile requires source-aware retrieval, versioned ingestion, citations,
freshness, authorization filtering, and comparable keyword, vector, and hybrid strategies.
Retrieval must remain separate from runtime orchestration and context policy.

## Decision

Use Azure AI Search as the v1.0 reference retrieval adapter. Use structure-aware chunking with
deterministic chunk identifiers derived from source identity, source version, and structural path.
Support keyword and vector retrieval, hybrid retrieval as the reference default, and semantic
reranking only when available and explicitly configured.

Every indexed chunk carries tenant scope, authorization labels, source identity, source version,
structural path, ingestion time, and freshness metadata. Queries apply mandatory tenant and
authorization filters before results enter context. Managed identity is the reference
authentication mechanism.

## Consequences

- Retrieval strategies can be compared over the same versioned dataset.
- Citations can preserve source and version lineage through context compaction.
- Embedding model and index schema changes require versioned re-ingestion.
- Azure AI Search service availability, semantic-ranking capacity, and index cost are deployment
  preflight concerns.
