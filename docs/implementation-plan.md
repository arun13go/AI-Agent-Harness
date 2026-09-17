# Implementation Plan

## Delivery Strategy

Build one thin, testable vertical slice before adding replaceable adapters. Every phase must leave a runnable template and evidence that its contracts still hold.

## Phase 0: Decisions and Contracts

**Goal:** Freeze the v1.0 boundary before scaffolding.

- Confirm Python as the first implementation language.
- Confirm MAF Harness and Foundry as the v1.0 runtime/platform pair.
- Write ADRs for runtime, hosting topology, state store, retrieval profile, and deployment command.
- Define configuration JSON Schema and normalized run-event schema.
- Define conformance tests for runtime, memory, state, approval, and audit adapters.
- Mark experimental MAF capabilities and post-v1.0 work explicitly.

**Exit:** schemas review cleanly and the architecture has no unresolved critical decision.

## Phase 1: Cloud Walking Skeleton

**Goal:** A clean clone deploys a usable Azure Dev environment while deterministic tests remain cloud-independent.

- Compose a MAF Harness agent with one read tool and one proposed action.
- Add bounded steps, timeout, typed errors, and structured run events.
- Add minimal `azure.yaml` and Bicep for the harness host, Foundry connection and managed identity.
- Keep in-memory session, memory, state, approval, and audit adapters for local tests.
- Add deterministic policy tests, including prompt-injection and approval bypass cases.
- Add a CLI or minimal API and VS Code Agent Inspector configuration.

**Exit:** `azd up` and **Deploy to Azure** create equivalent Azure Dev environments, the cloud smoke test passes, and local policy tests require no Azure connection.

## Phase 2: Grounding and Context

**Goal:** Prove configurable context behavior without coupling it to the runtime.

- Add retrieval and chunking contracts with a structure-aware default.
- Add source metadata, result-by-handle, context budget, and compaction policy.
- Add the answerability gate and all controlled routes.
- Add ingestion versioning and freshness metadata.
- Add test cases for weak, conflicting, stale, and sufficient evidence.

**Exit:** the same test dataset can compare chunking/context profiles, and weak evidence never becomes an unsupported confident answer.

## Phase 3: Azure Reference Adapters

**Goal:** Replace development infrastructure without changing agent policy.

- Harden the Foundry model adapter with usage metadata and normalized errors.
- Add selected Azure memory, durable state, artifact, and audit implementations.
- Export OpenTelemetry traces to Application Insights with payload capture off by default.
- Preserve tenant/user scope and optimistic concurrency in adapter contract tests.

**Exit:** local test and Azure adapter suites pass the same conformance tests.

## Phase 4: Evaluation and Operations

**Goal:** Make production readiness measurable.

- Create smoke and regression datasets under `evals/`.
- Measure relevance, task adherence, intent resolution, tool-call accuracy, answerability/groundedness, indirect attack resistance, latency, and cost.
- Separate infrastructure SLOs from outcome-quality thresholds and correlate by run ID.
- Add trace-to-regression workflow and versioned evaluation result artifacts.
- Gate pull requests on deterministic tests first; use a small stable evaluation smoke gate before broader scheduled evaluation.

**Exit:** a release candidate produces one go-live evidence report containing policy tests, evaluation comparison, red-team results, and telemetry checks.

## Phase 5: Azure Deployment Paths

**Goal:** Harden the Phase 1 Azure Dev deployment for repeatable customer use.

- Harden `azure.yaml` and modular Bicep with role assignments, outputs, tags, and environment overlays.
- Add preflight for authentication, provider registration, region/model availability, and quota.
- Add post-deploy smoke tests and documented `azd down` cleanup.
- Add GitHub Actions with workload identity federation for validation and deployment.
- Add a **Deploy to Azure** button using an ARM artifact generated from the canonical Bicep.
- Add CI checks for template drift and parity of parameters, RBAC, outputs and smoke tests across both paths.

**Exit:** both deployment paths succeed in clean resource groups and produce equivalent validated environments.

## Phase 6: Pilot and Harden

**Goal:** Validate repeatability with two materially different customer scenarios.

- Pilot one grounded read-heavy use case and one approval-gated action use case.
- Record every changed configuration field, replaced adapter, and bypass attempt.
- Tighten defaults, remove unused abstractions, and publish migration notes.
- Create the workshop labs from the verified clone-to-run path.

**Exit:** both pilots deploy from the same tagged template and retain the same governance/evaluation controls.

## Deferred Backlog

- Second runtime adapter and framework conformance report.
- Multi-agent workflows and background agents.
- Semantic response caching and model routing.
- AgentOps Accelerator export adapter.
- Private networking modules beyond documented enterprise integration points.
- Additional language implementations.

## Initial GitHub Work Items

| Order | Work item | Evidence |
|---|---|---|
| 1 | Define config and event schemas | Schema tests and examples |
| 2 | Scaffold MAF Azure Dev skeleton | Cloud smoke test and local policy tests |
| 3 | Implement governance policy core | Policy unit tests |
| 4 | Implement memory/state/approval dev adapters | Contract tests |
| 5 | Implement retrieval and answerability profile | Grounding test matrix |
| 6 | Add Foundry and Azure adapters | Integration tests |
| 7 | Add telemetry and dual scorecards | Trace and dashboard checks |
| 8 | Add Foundry evaluation assets | Baseline result artifact |
| 9 | Add azd/Bicep deployment | Deployment smoke test |
| 10 | Run two pilots and publish v1.0 | Pilot reports and tagged release |

## Known Tooling Prerequisite

The local Microsoft Foundry dependency check currently fails because Azure Developer CLI `1.23.7` cannot install the required `microsoft.foundry` extension. Upgrade azd to the current supported release (reported by the setup check as `1.34.1`) before scaffolding or deployment work, then rerun the Foundry dependency setup script.
