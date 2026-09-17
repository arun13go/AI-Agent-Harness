# Implementation Plan

## Purpose

This is the canonical delivery roadmap for implementing the Agent Harness. The [pattern catalogue](pattern-catalog.md) defines capability semantics and alternatives; this plan controls sequencing, scope, status, artifacts, and completion evidence.

## Delivery Strategy

Build thin, testable increments. Every phase must leave the repository in a coherent state, and every implementation must reference the pattern IDs it satisfies. Released Microsoft Agent Framework (MAF) Harness capabilities own agent execution; this repository adds policy, typed ports, reference adapters, configuration, evaluation, and deployment.

The first end-to-end milestone is deliberately narrow: an authenticated caller invokes an Azure-hosted MAF agent, the agent calls a typed read tool, proposes an approval-gated action, records correlated audit events, exports traces, and passes the same deterministic policy tests locally.

## Status and Tracking

| Status | Meaning |
|---|---|
| Not started | No implementation artifact exists |
| In progress | Work has started but exit evidence is incomplete |
| Blocked | A named dependency prevents progress |
| Complete | All exit evidence is recorded and passing |

Update phase status only when its evidence changes. GitHub issues should reference the phase and pattern IDs, while temporary command output and investigation notes stay under ignored `.local/logs/`.

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Tooling, decisions, and scope | In progress |
| 1 | Contracts, policy core, and cloud walking skeleton | Not started |
| 2 | Grounding and context profile | Not started |
| 3 | Durable Azure reference adapters | Not started |
| 4 | Evaluation and operations | Not started |
| 5 | Deployment and CI hardening | Not started |
| 6 | Pilots and v1.0 release | Not started |

## Phase 0: Tooling, Decisions, and Scope

**Goal:** Freeze the v1.0 boundary and establish a reproducible toolchain before scaffolding.

**Patterns:** all areas in the [pattern catalogue](pattern-catalog.md) as planning inputs.

- Confirm Python as the first implementation language.
- Confirm MAF Harness and Foundry as the v1.0 runtime/platform pair.
- Upgrade Azure Developer CLI and install the compatible `microsoft.foundry` extension.
- Pin supported Python, package manager, MAF, Azure SDK, lint, type-check, and test versions.
- Select the first Foundry model before writing model-dependent code.
- Complete ADRs for runtime, hosting topology, state store, retrieval profile, memory, and deployment where decisions remain open.
- Define configuration JSON Schema and normalized run-event schema.
- Define conformance tests for runtime, memory, state, approval, and audit adapters.
- Mark experimental MAF capabilities and post-v1.0 work explicitly.

**Artifacts:** dependency manifest and lock, tool-version policy, schemas, conformance-test design, remaining ADRs, and contributor setup instructions.

**Exit:** Foundry dependency setup passes, schemas review cleanly, the architecture has no unresolved critical decision, and a clean machine can install the pinned development toolchain.

## Phase 1: Cloud Walking Skeleton

**Goal:** A clean clone deploys a usable Azure Dev environment while deterministic tests remain cloud-independent.

**Patterns:** [MEM-01](pattern-catalog.md#mem-01), [MEM-02](pattern-catalog.md#mem-02), [STATE-01](pattern-catalog.md#state-01), [STATE-02](pattern-catalog.md#state-02), [STATE-03](pattern-catalog.md#state-03), [STATE-04](pattern-catalog.md#state-04), [STATE-05](pattern-catalog.md#state-05), [CTX-01](pattern-catalog.md#ctx-01), [CTX-02](pattern-catalog.md#ctx-02), [CTX-03](pattern-catalog.md#ctx-03), [CTX-04](pattern-catalog.md#ctx-04), [CTX-05](pattern-catalog.md#ctx-05), [SEC-01](pattern-catalog.md#sec-01), [SEC-02](pattern-catalog.md#sec-02), [SEC-03](pattern-catalog.md#sec-03), [SEC-04](pattern-catalog.md#sec-04), [SEC-05](pattern-catalog.md#sec-05), [SEC-06](pattern-catalog.md#sec-06), [SEC-07](pattern-catalog.md#sec-07), [POL-01](pattern-catalog.md#pol-01), [POL-02](pattern-catalog.md#pol-02), [POL-03](pattern-catalog.md#pol-03), [POL-04](pattern-catalog.md#pol-04), [POL-05](pattern-catalog.md#pol-05), [POL-06](pattern-catalog.md#pol-06), [POL-07](pattern-catalog.md#pol-07), [TOOL-01](pattern-catalog.md#tool-01), [TOOL-02](pattern-catalog.md#tool-02), [TOOL-03](pattern-catalog.md#tool-03), [TOOL-04](pattern-catalog.md#tool-04), [EVAL-01](pattern-catalog.md#eval-01), [OBS-01](pattern-catalog.md#obs-01), [OBS-02](pattern-catalog.md#obs-02), [OBS-04](pattern-catalog.md#obs-04), and [OBS-05](pattern-catalog.md#obs-05).

- Define identity, configuration, event, runtime, model, memory, state, artifact, approval, tool, audit, and evaluation types and protocols.
- Implement a code-owned registry that maps validated configuration names to installed implementations.
- Implement deterministic governance for identity scope, execution ceilings, tool allow-listing and risk tiers, approval, idempotency, and audit.
- Compose a MAF Harness agent with one read tool and one proposed action.
- Add bounded steps, timeout, typed errors, and structured run events.
- Add minimal `azure.yaml` and Bicep for the harness host, Foundry connection and managed identity.
- Add in-memory session, conversation memory, run state, checkpoint, artifact, approval, idempotency, and audit adapters for local tests.
- Add deterministic policy tests, including prompt-injection and approval bypass cases.
- Add a CLI or minimal API and VS Code Agent Inspector configuration.

**Artifacts:** core contracts, validated profiles, MAF and Foundry adapters, sample tools, in-memory adapters, minimal Azure infrastructure, debug configuration, and smoke tests.

**Exit:** schema and conformance tests pass; a local read path completes; an action pauses for approval, resumes once, and remains idempotent after replay; `azd up` and **Deploy to Azure** create equivalent Azure Dev environments; the cloud smoke test passes; local policy tests require no Azure connection.

## Phase 2: Grounding and Context

**Goal:** Prove configurable context behavior without coupling it to the runtime.

**Patterns:** [CTX-01](pattern-catalog.md#ctx-01), [CTX-02](pattern-catalog.md#ctx-02), [CTX-03](pattern-catalog.md#ctx-03), [CTX-04](pattern-catalog.md#ctx-04), [CTX-05](pattern-catalog.md#ctx-05), [CTX-06](pattern-catalog.md#ctx-06), [CTX-07](pattern-catalog.md#ctx-07), [RET-01](pattern-catalog.md#ret-01), [RET-02](pattern-catalog.md#ret-02), [RET-03](pattern-catalog.md#ret-03), [RET-04](pattern-catalog.md#ret-04), [RET-05](pattern-catalog.md#ret-05), [RET-06](pattern-catalog.md#ret-06), [RET-07](pattern-catalog.md#ret-07), [MEM-03](pattern-catalog.md#mem-03), and [EVAL-03](pattern-catalog.md#eval-03).

- Add retrieval and chunking contracts with a structure-aware default.
- Add keyword, vector, hybrid retrieval, and optional semantic reranking through the retrieval port.
- Add source metadata, freshness, result-by-handle, context budget, source labeling, and structured compaction policy.
- Add episodic-memory write policy for significant interactions only.
- Add the answerability gate and all controlled routes.
- Add ingestion versioning and freshness metadata.
- Add test cases for weak, conflicting, stale, and sufficient evidence.

**Artifacts:** grounded-assistant profile, Azure AI Search adapter, ingestion pipeline, answerability policy, and grounding fixtures.

**Exit:** the same test dataset can compare retrieval, chunking, and context profiles; citations retain source and version metadata; weak evidence never becomes an unsupported confident answer.

## Phase 3: Azure Reference Adapters

**Goal:** Replace development infrastructure without changing agent policy.

**Patterns:** [MEM-02](pattern-catalog.md#mem-02), [MEM-03](pattern-catalog.md#mem-03), [STATE-01](pattern-catalog.md#state-01), [STATE-02](pattern-catalog.md#state-02), [STATE-03](pattern-catalog.md#state-03), [STATE-04](pattern-catalog.md#state-04), [STATE-05](pattern-catalog.md#state-05), [CACHE-02](pattern-catalog.md#cache-02), [CACHE-03](pattern-catalog.md#cache-03), [OBS-01](pattern-catalog.md#obs-01), [OBS-02](pattern-catalog.md#obs-02), [OBS-03](pattern-catalog.md#obs-03), [OBS-04](pattern-catalog.md#obs-04), and [OBS-05](pattern-catalog.md#obs-05).

- Harden the Foundry model adapter with usage metadata and normalized errors.
- Add selected Azure conversation memory, episodic memory, durable state, artifact, idempotency, cache, and audit implementations.
- Export OpenTelemetry traces to Application Insights with payload capture off by default.
- Cache only immutable retrieval artifacts and authorization-safe read-tool results.
- Preserve tenant/user scope, provenance, retention, deletion, optimistic concurrency, and append-only audit behavior in adapter contract tests.

**Artifacts:** Azure storage adapters, retention configuration, infrastructure modules, telemetry export, and integration tests.

**Exit:** local and Azure adapter suites pass the same conformance tests, including cross-tenant, stale-write, expiry, deletion, retry, and duplicate-delivery cases.

## Phase 4: Evaluation and Operations

**Goal:** Make production readiness measurable.

**Patterns:** [EVAL-01](pattern-catalog.md#eval-01), [EVAL-02](pattern-catalog.md#eval-02), [EVAL-03](pattern-catalog.md#eval-03), [EVAL-04](pattern-catalog.md#eval-04), [EVAL-05](pattern-catalog.md#eval-05), [EVAL-06](pattern-catalog.md#eval-06), [EVAL-07](pattern-catalog.md#eval-07), [EVAL-08](pattern-catalog.md#eval-08), [OBS-01](pattern-catalog.md#obs-01), [OBS-02](pattern-catalog.md#obs-02), [OBS-03](pattern-catalog.md#obs-03), [OBS-04](pattern-catalog.md#obs-04), and [OBS-05](pattern-catalog.md#obs-05).

- Create smoke and regression datasets under `evals/`.
- Measure relevance, task adherence, intent resolution, tool-call accuracy, answerability/groundedness, indirect attack resistance, latency, and cost.
- Separate infrastructure SLOs from outcome-quality thresholds and correlate by run ID.
- Add reviewed trace-to-regression workflow and versioned evaluation result artifacts recording dataset, prompt, model, policy, evaluator, and candidate versions.
- Gate pull requests on deterministic tests first; use a small stable evaluation smoke gate before broader scheduled evaluation.

**Artifacts:** datasets, evaluators, thresholds, runner, operational and quality scorecards, baseline, and comparison reports.

**Exit:** a release candidate produces one go-live evidence report containing policy tests, evaluation comparison, red-team results, telemetry checks, latency, and cost.

## Phase 5: Azure Deployment Paths

**Goal:** Harden the Phase 1 Azure Dev deployment for repeatable customer use.

**Patterns:** [SEC-01](pattern-catalog.md#sec-01), [SEC-05](pattern-catalog.md#sec-05), [SEC-07](pattern-catalog.md#sec-07), with [SEC-08](pattern-catalog.md#sec-08) documented as a production overlay.

- Harden `azure.yaml` and modular Bicep with role assignments, outputs, tags, and environment overlays.
- Add preflight for authentication, provider registration, region/model availability, and quota.
- Add post-deploy smoke tests and documented `azd down` cleanup.
- Add GitHub Actions with workload identity federation for validation and deployment.
- Add a **Deploy to Azure** button using an ARM artifact generated from the canonical Bicep.
- Add CI checks for template drift and parity of parameters, RBAC, outputs and smoke tests across both paths.
- Document private-networking and enterprise landing-zone integration without deploying the customer's landing zone.

**Artifacts:** hardened infrastructure, generated ARM artifact, deployment button, CI workflows, preflight checks, smoke tests, and runbooks.

**Exit:** both deployment paths succeed in clean resource groups and produce equivalent validated environments; CI rejects generated-template drift.

## Phase 6: Pilot and Harden

**Goal:** Validate repeatability with two materially different customer scenarios.

**Patterns:** all v1.0 patterns in the [pattern catalogue](pattern-catalog.md).

- Pilot one grounded read-heavy use case and one approval-gated action use case.
- Record every changed configuration field, replaced adapter, and bypass attempt.
- Tighten defaults, remove unused abstractions, and publish migration notes.
- Create the workshop labs from the verified clone-to-run path.
- Produce a signed-off v1.0 evidence report and tagged release.

**Artifacts:** pilot reports, updated guidance, release evidence, changelog, and v1.0 tag.

**Exit:** both pilots deploy from the same tagged template and retain the same governance/evaluation controls; a clean clone meets the repository definition of done.

## Deferred Backlog

- [MEM-04](pattern-catalog.md#mem-04) semantic-memory Azure implementation and [MEM-05](pattern-catalog.md#mem-05) preference-memory Azure implementation.
- [CTX-07](pattern-catalog.md#ctx-07) hierarchical context implementation and [STATE-06](pattern-catalog.md#state-06) event-sourced reconstruction.
- [CACHE-01](pattern-catalog.md#cache-01) exact-key model caching and [CACHE-04](pattern-catalog.md#cache-04) semantic response caching.
- [POL-08](pattern-catalog.md#pol-08) dynamic model and tool routing.
- Second runtime adapter and framework conformance report.
- Multi-agent workflows and background agents.
- AgentOps Accelerator export adapter.
- Private networking modules beyond documented enterprise integration points.
- Additional language implementations.

## Initial GitHub Work Items

| Order | Work item | Phase | Pattern references | Evidence |
|---|---|---|---|---|
| 1 | Upgrade and pin the development toolchain | 0 | All | Dependency setup output |
| 2 | Define identity, configuration, and event schemas | 1 | [SEC-02](pattern-catalog.md#sec-02), [SEC-04](pattern-catalog.md#sec-04), [OBS-04](pattern-catalog.md#obs-04) | Schema and type checks |
| 3 | Define adapter protocols and conformance harness | 1 | [Memory](pattern-catalog.md#memory-patterns), [state](pattern-catalog.md#state-management-patterns), [tools](pattern-catalog.md#tool-patterns), [evaluation](pattern-catalog.md#evaluation-patterns) | Contract-test collection |
| 4 | Implement mandatory governance policy | 1 | [Governance and policy patterns](pattern-catalog.md#governance-and-policy-patterns) | Policy test suite |
| 5 | Compose local MAF vertical slice | 1 | [TOOL-01](pattern-catalog.md#tool-01), [TOOL-02](pattern-catalog.md#tool-02), [TOOL-03](pattern-catalog.md#tool-03), [TOOL-04](pattern-catalog.md#tool-04) | Local behavior smoke test |
| 6 | Deploy Azure Dev vertical slice | 1 | [SEC-01](pattern-catalog.md#sec-01), [OBS-01](pattern-catalog.md#obs-01), [OBS-02](pattern-catalog.md#obs-02) | Cloud smoke and trace checks |
| 7 | Implement grounded-assistant profile | 2 | [Context](pattern-catalog.md#context-management-patterns), [retrieval](pattern-catalog.md#retrieval-and-grounding-patterns), [MEM-03](pattern-catalog.md#mem-03) | Grounding matrix |
| 8 | Add durable Azure adapters | 3 | [MEM-02](pattern-catalog.md#mem-02), [MEM-03](pattern-catalog.md#mem-03), [state](pattern-catalog.md#state-management-patterns), [CACHE-02](pattern-catalog.md#cache-02), [CACHE-03](pattern-catalog.md#cache-03) | Shared conformance results |
| 9 | Add evaluation and operational scorecards | 4 | [Evaluation](pattern-catalog.md#evaluation-patterns), [observability](pattern-catalog.md#observability-and-audit-patterns) | Baseline report |
| 10 | Harden dual deployment and CI | 5 | [SEC-01](pattern-catalog.md#sec-01), [SEC-05](pattern-catalog.md#sec-05), [SEC-07](pattern-catalog.md#sec-07) | Clean deployment comparison |
| 11 | Run pilots and publish v1.0 | 6 | [All v1.0 patterns](pattern-catalog.md) | Pilot and release reports |

## Known Tooling Prerequisite

The local Microsoft Foundry dependency check currently fails because Azure Developer CLI `1.23.7` cannot install the required `microsoft.foundry` extension. Upgrade azd to the current supported release (reported by the setup check as `1.34.1`) before scaffolding or deployment work, then rerun the Foundry dependency setup script.
