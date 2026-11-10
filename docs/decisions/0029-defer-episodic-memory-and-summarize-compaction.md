# ADR 0029: Defer Episodic Memory and `summarize` Compaction

- **Status:** Accepted
- **Date:** 10 November 2026
- **Builds on:** [ADR 0018](0018-bounded-context-plans.md), [ADR 0028](0028-no-flag-producer-or-caveat-in-v1.md)

## Purpose and scope

The Phase 2 deliverable lists compaction and an episodic-memory policy. Bounded context
(`truncate-oldest`) is built. This ADR defers the two remaining items and records why, so closing
Phase 2 does not leave them as silent gaps.

## Decision

- **Episodic memory moves to Phase 3.** Remembering earlier interactions needs durable storage
  scoped by tenant and user, retention, deletion and replay tests. Those are the Phase 3 Cosmos DB
  memory adapters. A policy written now could not be tested against real storage, so it is
  designed with them. Until then the `MemoryStore` port and its in-memory implementation remain
  the only memory contract, and retrieved content is never written to memory.
- **`summarize` compaction moves to Phase 4.** `truncate-oldest` already keeps context within the
  budget. Summarizing history needs an extra model call, which adds cost and latency and can turn
  untrusted text into trusted-looking context. It should ship only with an evaluation that shows it
  does not reduce answer quality, and evaluation is Phase 4. The profile loader keeps rejecting
  `compaction: summarize` instead of silently truncating.

## Consequences

- **Phase 2 is complete without them.** Chat history is bounded by truncation only, and the
  assistant does not recall earlier sessions.
- **Revisit** episodic memory when the Phase 3 stores exist, and `summarize` when Phase 4 can
  measure its effect.
