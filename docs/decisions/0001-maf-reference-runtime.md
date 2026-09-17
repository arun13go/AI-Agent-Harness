# ADR 0001: Use MAF Harness as the Reference Runtime

- **Status:** Accepted
- **Date:** 13 September 2026

## Context

The template must be reusable and should permit future framework adapters without owning a new runtime. MAF now provides a released Harness with tool invocation, sessions, context providers, compaction, planning/todos, modes, approvals, and OpenTelemetry. Foundry provides the reference Azure platform services.

## Decision

Use the Python MAF Harness as the only v1.0 runtime adapter. Keep policy and infrastructure variation behind typed contracts and conformance tests. Treat Foundry as the reference model, hosting, evaluation, and tracing platform. A future runtime is added through a code adapter, not claimed as a configuration-only substitution.

Experimental MAF capabilities are disabled by default and cannot be required by the v1.0 path.

## Consequences

- The template remains thin and benefits from MAF product improvements.
- v1.0 delivers one credible path instead of two incomplete flavours.
- Portability is measurable through contracts but not cost-free.
- Framework-specific features may remain adapter-specific.
- A second framework requires an ADR and a passing conformance report.
