# ADR 0033: Phase 3 Scope: Wire Durable Stores When Used, Defer Memory

- **Status:** Accepted
- **Date:** 11 November 2026
- **Builds on:** [ADR 0029](0029-defer-episodic-memory-and-summarize-compaction.md), [ADR 0031](0031-lightweight-by-design.md), [ADR 0032](0032-phase-3-storage-design.md)
- **Changes:** ADR 0029 moved episodic memory to Phase 3; it now waits for a consumer.

## Decision

- **Cosmos stores stay unwired until a composition needs them.** The hosted profiles are stateless
  and only the `action-demo` command composes state stores. A factory and config block with no
  production caller would be dead code. The stores are built and conformance-tested; wiring is
  added with the first profile that needs durable state (the action-agent pilot).
- **`memory.adapter` and `state.adapter` refuse `cosmos-db` at load.** A probe showed the setting
  loaded and was silently ignored, so a user could believe state was durable when it was in process
  memory. The schema still reserves the value, as it does for `compaction: summarize`.
- **No retention setting beyond what exists.** Audit expiry is the `cosmosAuditRetentionDays`
  deployment parameter (default 90, to be confirmed). Other records expire at their own
  `expires_at` plus a 7-day grace. `memory.ttlDays` stays validated but unused until memory exists.
- **Episodic memory is not built.** Nothing reads or writes memory. Its policy (what may be
  remembered, per-user scope, no retrieved content) is designed with its first consumer, because a
  policy with no caller cannot be tested. The `MemoryStore` port stays as the named contract.
- **Tenant deletion is not built.** It needs a `delete` backend method and a consumer.

## Not built

Wiring or a factory for the Cosmos stores, a `MemoryStore` implementation, expiry and deletion
conformance tests (they need a store that expires or deletes), and a retention config block.

## Limits

- Phase 3's stated exit (in-memory and Azure adapters pass the same isolation, concurrency,
  expiry, deletion and replay tests) is met for isolation, concurrency and replay on a fake
  backend only. Expiry and deletion are not covered, and nothing has run against live Cosmos DB
  because tenant policy blocks public access to the account (ADR 0032).
- Until a profile wires them, no deployment uses the Cosmos stores.
