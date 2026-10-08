# Adapter Contract and Conformance Design

## Purpose

Typed protocols define the callable shape of replaceable components. Shared conformance suites
define the behavior that types cannot express. An adapter is selectable by configuration only after
it passes its applicable suite.

## Common fixtures

Every suite uses two tenant scopes, two subject scopes in one tenant, deterministic time, stable
run/correlation identifiers, cancellation, and normalized failures. Azure suites run the same cases
as in-memory suites plus service-specific integration checks.

## Required suites

| Contract | Required behavior |
|---|---|
| Model client | request immutability, tool-call normalization, usage metadata, streaming order, cancellation, timeout, rate-limit/auth/content-filter error normalization |
| Retrieval and chunking | tenant/ACL filtering, stable source/version metadata, deterministic chunk identifiers, freshness, citation preservation, untrusted-content labeling |
| Context policy | hard token budget, source labels, deterministic overflow route, result handles, no promotion of untrusted data to instructions |
| Memory | tenant/subject isolation, provenance, TTL, deletion, duplicate writes, untrusted-content labeling |
| Run state | optimistic concurrency, legal transitions, checkpoint/resume, stale writes, cancellation, duplicate delivery, approval expiry |
| Approval | immutable proposal reference, approver scope, decision expiry, replay behavior, no execution before approval |
| Tool | schema validation, allow-list enforcement, risk tier, identity propagation, idempotency for act-tier tools, normalized errors |
| Audit | append-only behavior, monotonic sequence, correlation fields, duplicate-event handling, payload exclusion |
| Telemetry | trace correlation, bounded attributes, payload capture off by default, exporter failure visibility, no business payload leakage |

## Test organization

```text
tests/
  contracts/       # Protocol surface and registry tests
  conformance/     # Reusable behavior suites
  schema/          # JSON Schema and example validation
```

Each conformance suite is an abstract pytest class or fixture-driven function set. Implementations
provide only adapter factories and service cleanup. Tests must not branch on adapter type.

## Error semantics

Adapters raise contract-owned typed errors for invalid input, authorization, conflict, not found,
timeout, throttling, cancellation, unavailable dependency, and unexpected provider failure.
Provider exceptions never cross the adapter boundary unchanged. Retries are allowed only when the
normalized error explicitly marks the operation retryable.

## Phase 1 entry criteria

Phase 1 first implements in-memory adapters for state, memory, approval, idempotency, and audit.
Those adapters establish executable conformance suites before Azure implementations are added.
