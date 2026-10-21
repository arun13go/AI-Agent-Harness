# ADR 0014: Export Allowlisted Operational Spans Only

- **Status:** Accepted
- **Date:** 21 October 2026

## Decision

Compose an explicit OpenTelemetry trace provider with the Azure Monitor exporter. Pin the
already-resolved exporter version directly. Do not enable the server SDK's automatic
observability, logging export, metric export, or content capture. The operator approved reuse
of the existing development Application Insights resource; keep its connection string only in
deployment settings and ignored azd state.

Wrap the exporter with `MetadataOnlyExporter`. Rebuild each exported span with fixed resource
metadata and bounded operation vocabulary, preserving span/parent IDs, timings, kind, status
code, numeric token counts, and HTTP status. Drop arbitrary attributes, dynamic names, events,
links, baggage, trace state, instrumentation metadata, and status descriptions. This privacy
boundary applies even if upstream instrumentation captures sensitive content.

Create an HTTP server span and propagate its context through the released SDK middleware.
MAF retains that active span as its parent rather than creating a synthetic sibling. Audit
events share its trace ID. Readiness does not invoke a model or produce an agent-quality signal.

Missing exporter configuration fails startup. Report export failures explicitly; disable
offline exporter storage. Operational traces are not the append-only audit store and are not
an evaluation dataset. Audit remains in memory in Phase 1.

## Validation

Tests inject prompts, tokens, caller identifiers, events, resource values, and exception details
and assert they cannot reach the exporter. HTTP integration verifies connected HTTP/agent/tool
parentage and audit correlation. A live Azure trace query is still required after deployment.

## Consequences

The default console profile remains unchanged. Hosted packaging explicitly selects the
Application Insights exporter. This restricted trace schema intentionally omits conversation
content and some Foundry portal dimensions; quality evaluation and rich Inspector compatibility
are separate acceptance work, not implied by operational tracing.
