# ADR 0017: Deterministic Chunk Identity

- **Status:** Accepted
- **Date:** 3 November 2026
- **Refines:** [ADR 0007](0007-azure-ai-search-reference-retrieval.md)

## Purpose and scope

ADR 0007 requires chunk identifiers derived from source identity, source version and structural
path. This ADR fixes the exact derivation and path grammar so every chunker and index adapter
produces the same identifiers. It covers identity only, not ranking, embeddings or index schema.

## Decision

- **Identity:** `chk_` plus the first 32 hex characters of SHA-256 over the JSON array
  `[tenantScope, sourceId, sourceVersion, chunkPath]`.
- **Tenant is part of identity.** ADR 0007 omitted it. Two tenants may ingest the same source ID and
  version; without the tenant their chunks would collide in a shared index.
- **Authorization labels are not part of identity.** Re-ingesting a source with changed labels must
  overwrite the existing chunk. If labels were hashed, a changed ACL would add a second chunk and
  leave the old, possibly more permissive, copy retrievable.
- **Content is not part of identity.** The source version is the change contract: a producer must
  change `sourceVersion` when content changes. Identical version and path with different content is a
  producer error and replaces the chunk on upsert. Hashing content would leave stale chunks behind.
- **Path grammar:** `<document path>#<heading slugs joined by "/">`. Slugs are case-folded, keep only
  letters, digits, `_`, `.` and `-`, and are at most 64 characters, so heading text cannot inject `/`,
  `#`, `~` or `..`. Text before the first heading is `preamble`; an empty heading is `section`.
  Document paths may not contain `#`. A repeated path gains `~2`, `~3` and so on, in document order.
- **Normalization before chunking:** Unicode NFC and `\n` line endings, so the same text yields the
  same chunks regardless of platform or editor.
- **Structure rules (RET-01):** ATX headings start sections; fenced code and tables stay whole;
  oversized blocks split at line boundaries, repeating a table's two header rows; a heading stays
  with the first piece of its body; sections without body text are skipped, because their heading
  is already captured in descendant paths.
- **Bounds:** at most 1,000,000 source characters, 10,000 chunks per source, 200–16,000 characters per
  chunk, 64 distinct authorization labels. Violations raise `InputValidationError`.
- **Content is inert data.** Chunk text never affects identity, paths, labels or flags, and chunks
  carry `untrusted-retrieved-content`.

## Consequences

- Re-ingesting an unchanged version produces identical chunks, so upserts are idempotent.
- Inserting an earlier section keeps unrelated chunk identifiers stable; only repeated heading
  paths after the insertion shift their `~n` suffix.
- Setext headings and HTML blocks are not recognized; they stay in the surrounding section as text.
- A shared conformance suite (`tests/conformance/chunker_suite.py`) defines these rules for any
  chunker; the Azure AI Search ingestion path must pass it before selection.
- Stale-content protection depends on producers versioning sources; an index adapter should also
  delete chunks of superseded versions.
