# Agent Harness Pattern Catalogue

## Purpose

This catalogue defines the implementation patterns supported or anticipated by the Agent Harness. It complements the [implementation plan](implementation-plan.md): the plan controls delivery order, while this document defines where each capability belongs, which semantics are mandatory, and which implementation is selected for v1.0.

Pattern IDs are stable references for implementation tasks, tests, ADRs, and GitHub issues. A pattern marked **v1.0** is part of the initial release. **Contract only** means its interface and conformance requirements are defined in v1.0 but an Azure implementation may follow later. **Deferred** means it is outside the v1.0 critical path.

The **Reference** column links to first-party Microsoft documentation describing an established pattern or its underlying platform semantics. A pattern labeled **(new)** is a template-specific construct for which no equivalent authoritative pattern was found; it must receive a separate design document or ADR before implementation. A reference supports the concept, but this catalogue remains authoritative for the harness-specific contract and controls.

## Pattern Rules

- Compose released Microsoft Agent Framework (MAF) Harness behavior; do not build a competing execution loop.
- Keep policy separate from mechanism. Configuration selects only implementations registered by code.
- Enforce identity scope, authorization, execution ceilings, approval, idempotency, audit correlation, and safe telemetry defaults in code.
- Treat retrieved content, memory, and tool output as untrusted data, never as system or developer instructions.
- Keep session history, memory, durable run state, artifacts, and audit records as separate concepts with separate retention rules.
- Require every adapter to pass the shared contract tests before it can be selected by a profile.
- Never cache consequential actions or use conversational memory as the system of record for an action.

## Capability Map

| Area | Patterns | Planned code boundary |
|---|---|---|
| Runtime | MAF execution, cancellation, streaming, bounded runs | `src/adapters/maf/` |
| Memory | Working, conversation, episodic, semantic, preference, procedural | `src/core/contracts/memory.py`, `src/adapters/storage/memory/` |
| State | Session, durable run, checkpoint/resume, idempotency, artifacts | `src/core/contracts/state.py`, `src/adapters/storage/state/` |
| Context | Budgeting, source labeling, compaction, result handles | `src/core/context/`, `src/adapters/maf/context/` |
| Retrieval | Keyword, vector, hybrid, semantic ranking, chunking | `src/core/contracts/retrieval.py`, `src/adapters/retrieval/` |
| Security | Identity, authorization, tenant isolation, data protection | `src/core/security/` |
| Policy | Execution limits, tool tiers, approval, answerability, retention | `src/core/governance/` |
| Tools | Read, propose, act, function tools, MCP tools | `src/core/contracts/tools.py`, `src/adapters/tools/` |
| Cache | Exact-key, retrieval artifact, safe tool-result, semantic response | `src/core/contracts/cache.py`, `src/adapters/storage/cache/` |
| Evaluation | Outcome, grounding, answerability, tools, safety, latency, cost | `evals/`, `src/adapters/foundry/evaluation/` |
| Observability | Traces, metrics, audit events, operational and quality health | `src/core/events/`, `src/adapters/telemetry/` |

## Memory Patterns

Memory stores information for later use. It does not own resumable execution state or immutable audit history.

| ID | Pattern | Purpose | Lifetime | v1.0 disposition | Reference |
|---|---|---|---|---|---|
| <a id="mem-01"></a>MEM-01 | Working memory | Intermediate values needed during one run | One run | v1.0, in-process | [Agent Framework memory](https://learn.microsoft.com/agent-framework/get-started/memory) |
| <a id="mem-02"></a>MEM-02 | Conversation memory | Recent turns and bounded session summaries | One session with TTL | v1.0, in-memory and Azure reference adapter | [Chat history memory provider](https://learn.microsoft.com/agent-framework/concepts/agents/conversations/chat-history-memory-provider) |
| <a id="mem-03"></a>MEM-03 | Episodic memory | Significant past interactions, decisions, and outcomes | Across sessions | v1.0, scoped Azure reference adapter | [Agent Framework memory with Azure Cosmos DB](https://learn.microsoft.com/agent-framework/integrations/by-component/context-providers/azure-cosmos) |
| <a id="mem-04"></a>MEM-04 | Semantic memory | Durable facts derived from approved sources | Long term, freshness controlled | Contract only | [Memory in Foundry Agent Service](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-memory) |
| <a id="mem-05"></a>MEM-05 | Preference memory | Explicit user preferences and choices | Long term, user controlled | Contract only | [Memory in Foundry Agent Service](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-memory) |
| <a id="mem-06"></a>MEM-06 | Procedural memory | Versioned instructions and reusable workflows | Application release lifetime | v1.0 through configuration and code registry | [Agent Framework memory with Azure Cosmos DB](https://learn.microsoft.com/agent-framework/integrations/by-component/context-providers/azure-cosmos) |

Every memory record must carry tenant and subject scope, provenance, creation time, retention or expiry, sensitivity classification, and policy version. Writes to durable memory require an explicit write policy; model output alone cannot silently become trusted memory.

Required memory operations are scoped read, write, query, delete, and expiry. Conformance tests cover cross-tenant isolation, provenance preservation, retention, deletion, duplicate writes, and untrusted-content labeling.

## State Management Patterns

State records what the system is doing. It is authoritative for retry and resume, unlike conversational memory.

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="state-01"></a>STATE-01 | Session state | Bind messages and transient context to a session | v1.0 | [Chat history memory provider](https://learn.microsoft.com/agent-framework/concepts/agents/conversations/chat-history-memory-provider) |
| <a id="state-02"></a>STATE-02 | Durable run state | Persist lifecycle status and normalized transitions | v1.0 | [Manage state for long-running agents](https://learn.microsoft.com/azure/foundry/agents/how-to/manage-task-state) |
| <a id="state-03"></a>STATE-03 | Checkpoint and resume | Continue safely after approval, interruption, or failure | v1.0 | [Agent Framework workflow checkpoints](https://learn.microsoft.com/agent-framework/workflows/checkpoints) |
| <a id="state-04"></a>STATE-04 | Idempotency ledger | Prevent duplicate consequential effects | v1.0 | [Designing Azure Functions for identical input](https://learn.microsoft.com/azure/azure-functions/functions-idempotent) |
| <a id="state-05"></a>STATE-05 | Artifact store | Persist large results by immutable handle | v1.0 | [Claim-Check pattern](https://learn.microsoft.com/azure/architecture/patterns/claim-check) |
| <a id="state-06"></a>STATE-06 | Event-sourced reconstruction | Rebuild state entirely from events | Deferred | [Event Sourcing pattern](https://learn.microsoft.com/azure/architecture/patterns/event-sourcing) |

Durable state uses optimistic concurrency and explicit state transitions. An act-tier tool must persist its proposal and checkpoint before approval, and its idempotency record before or atomically with the external effect.

Conformance tests cover stale writes, retry, resume, duplicate delivery, approval expiry, cancellation, partial failure, and tenant isolation.

## Context Management Patterns

Context determines what reaches the model for a specific invocation.

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="ctx-01"></a>CTX-01 | Token budget | Reserve bounded space for instructions, history, evidence, tools, and output | v1.0 | [Agent Framework compaction](https://learn.microsoft.com/agent-framework/concepts/agents/conversations/compaction) |
| <a id="ctx-02"></a>CTX-02 | Sliding window | Retain the most recent relevant conversation turns | v1.0 | [Compaction strategies](https://learn.microsoft.com/agent-framework/concepts/agents/conversations/compaction#compaction-strategies) |
| <a id="ctx-03"></a>CTX-03 | Structured compaction | Summarize older context while preserving decisions and provenance | v1.0 using MAF capabilities | [Agent Framework compaction](https://learn.microsoft.com/agent-framework/concepts/agents/conversations/compaction) |
| <a id="ctx-04"></a>CTX-04 | Result by handle (new) | Keep large tool or retrieval payloads outside the prompt | v1.0 | No external source; design document required |
| <a id="ctx-05"></a>CTX-05 | Source labeling | Mark memory, retrieval, and tool content as untrusted data | v1.0, mandatory | [Agent security with FIDES](https://learn.microsoft.com/agent-framework/agents/security#labeling-your-data-sources) |
| <a id="ctx-06"></a>CTX-06 | Context relevance selection | Select evidence according to task and policy | v1.0 | [Adding context providers](https://learn.microsoft.com/agent-framework/journey/adding-context-providers) |
| <a id="ctx-07"></a>CTX-07 | Hierarchical context (new) | Compose organization, user, task, and run context layers | Contract only | No external source; design document required |

Context policy must be deterministic where possible and observable without recording sensitive payloads. Tests cover budget overflow, compaction fidelity, instruction injection in retrieved content, stale evidence, missing handles, and source-priority conflicts.

## Retrieval and Grounding Patterns

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="ret-01"></a>RET-01 | Structure-aware chunking | Preserve headings, tables, and document boundaries | v1.0 default | [Chunk documents for RAG and vector search](https://learn.microsoft.com/azure/search/vector-search-how-to-chunk-documents) |
| <a id="ret-02"></a>RET-02 | Keyword retrieval | Exact terms, identifiers, and names | v1.0 | [RAG in Azure AI Search](https://learn.microsoft.com/azure/search/retrieval-augmented-generation-overview) |
| <a id="ret-03"></a>RET-03 | Vector retrieval | Semantic similarity | v1.0 | [Vector search in Azure AI Search](https://learn.microsoft.com/azure/search/vector-search-overview) |
| <a id="ret-04"></a>RET-04 | Hybrid retrieval | Combine keyword and vector candidates | v1.0 default | [Hybrid search in Azure AI Search](https://learn.microsoft.com/azure/search/hybrid-search-overview) |
| <a id="ret-05"></a>RET-05 | Semantic reranking | Improve ordering of retrieved candidates | v1.0 when supported by selected service | [Semantic ranking in Azure AI Search](https://learn.microsoft.com/azure/search/semantic-search-overview) |
| <a id="ret-06"></a>RET-06 | Versioned ingestion | Track source version, freshness, and re-indexing | v1.0 | [Content preparation for RAG](https://learn.microsoft.com/azure/search/retrieval-augmented-generation-overview#content-preparation-for-rag) |
| <a id="ret-07"></a>RET-07 | Answerability gate (new) | Route insufficient evidence safely | v1.0 for grounded profiles | No external source; design document required |

The answerability routes are answer, retrieve again, clarify, caveat, abstain, and escalate. Tests use sufficient, weak, conflicting, stale, malicious, and missing evidence.

## Security Patterns

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="sec-01"></a>SEC-01 | Workload identity | Use managed identity and passwordless Azure SDK authentication | v1.0, mandatory | [Secretless authentication for Azure resources](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/secretless-authentication) |
| <a id="sec-02"></a>SEC-02 | Caller identity propagation | Preserve tenant, user, run, and permission tier | v1.0, mandatory | [Agent security with FIDES](https://learn.microsoft.com/agent-framework/agents/security) |
| <a id="sec-03"></a>SEC-03 | Authorization before retrieval and tools | Enforce access before data or action execution | v1.0, mandatory | [Least privilege for AI agents](https://learn.microsoft.com/security/zero-trust/sfi/least-privilege-for-ai-agents) |
| <a id="sec-04"></a>SEC-04 | Tenant and user isolation | Scope memory, state, cache, retrieval, and audit | v1.0, mandatory | [Tenancy models for multitenant solutions](https://learn.microsoft.com/azure/architecture/guide/multitenant/considerations/tenancy-models#tenant-isolation) |
| <a id="sec-05"></a>SEC-05 | Least-privilege tool credentials | Give each adapter only required permissions | v1.0, mandatory | [Least privilege for AI agents](https://learn.microsoft.com/security/zero-trust/sfi/least-privilege-for-ai-agents) |
| <a id="sec-06"></a>SEC-06 | Input and output protection | Validate schemas, label untrusted data, and apply safety controls | v1.0, mandatory | [Agent security with FIDES](https://learn.microsoft.com/agent-framework/agents/security) |
| <a id="sec-07"></a>SEC-07 | Secretless configuration | Resolve endpoints and identity through environment and azd outputs | v1.0, mandatory | [Passwordless connections for Azure services](https://learn.microsoft.com/azure/developer/intro/passwordless-overview) |
| <a id="sec-08"></a>SEC-08 | Private networking overlay | Add private endpoints and network isolation | Production guidance; implementation deferred | [Azure Private Link overview](https://learn.microsoft.com/azure/private-link/private-link-overview) |

Security controls cannot be disabled by ordinary profile configuration. Tests cover identity substitution, cross-tenant access, unauthorized tools, prompt injection, payload leakage, and unsafe telemetry capture.

## Governance and Policy Patterns

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="pol-01"></a>POL-01 | Hard execution ceilings | Bound steps, elapsed time, tool calls, and token or cost budgets | v1.0, mandatory | [Resilience for long-running Foundry agents](https://learn.microsoft.com/azure/foundry/agents/concepts/long-running-agent-resilience) |
| <a id="pol-02"></a>POL-02 | Tool allow-list | Restrict available tools by profile and caller permission | v1.0, mandatory | [Least privilege for AI agents](https://learn.microsoft.com/security/zero-trust/sfi/least-privilege-for-ai-agents) |
| <a id="pol-03"></a>POL-03 | Tool risk tiers (new) | Classify tools as read, propose, or act | v1.0, mandatory | No external source; design document required |
| <a id="pol-04"></a>POL-04 | Human approval | Require an identified decision before act-tier execution | v1.0, mandatory | [Agent Framework tool approval](https://learn.microsoft.com/agent-framework/agents/tools/tool-approval) |
| <a id="pol-05"></a>POL-05 | Idempotent action policy | Require stable keys and replay-safe behavior | v1.0, mandatory | [Designing Azure Functions for identical input](https://learn.microsoft.com/azure/azure-functions/functions-idempotent) |
| <a id="pol-06"></a>POL-06 | Answerability policy (new) | Select safe route when evidence is insufficient | v1.0 for grounded profiles | No external source; design document required |
| <a id="pol-07"></a>POL-07 | Retention policy | Control memory, state, artifacts, cache, and telemetry lifetimes | v1.0 | [Azure Well-Architected monitoring data retention](https://learn.microsoft.com/azure/well-architected/design-guides/monitoring#phase-2---telemetry-data-collection-and-storage) |
| <a id="pol-08"></a>POL-08 | Model and tool routing | Select by policy, capability, risk, latency, and cost | Deferred | [AI agent orchestration patterns](https://learn.microsoft.com/azure/architecture/ai-ml/guide/ai-agent-design-patterns) |

Policy decisions emit normalized events containing rule identifiers, decision results, reasons, and correlation identifiers without business payloads by default.

## Tool Patterns

| ID | Pattern | Required behavior | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="tool-01"></a>TOOL-01 | Read tool | Typed input/output, authorization, timeout, normalized errors | v1.0 | [Agent Framework tools overview](https://learn.microsoft.com/agent-framework/agents/tools/) |
| <a id="tool-02"></a>TOOL-02 | Propose tool (new) | Produce a reviewable proposal without external mutation | v1.0 | No external source; design document required |
| <a id="tool-03"></a>TOOL-03 | Act tool | Approval, idempotency, least privilege, durable result | v1.0 | [Agent Framework tool approval](https://learn.microsoft.com/agent-framework/agents/tools/tool-approval) |
| <a id="tool-04"></a>TOOL-04 | Function tool adapter | Register local or hosted functions through typed contracts | v1.0 | [Agent Framework tools overview](https://learn.microsoft.com/agent-framework/agents/tools/) |
| <a id="tool-05"></a>TOOL-05 | MCP tool adapter | Apply the same policy envelope to approved MCP tools | v1.0 | [Using MCP tools with Agent Framework](https://learn.microsoft.com/agent-framework/agents/tools/local-mcp-tools) |
| <a id="tool-06"></a>TOOL-06 | Long-running tool | Checkpoint, cancellation, progress, and durable completion | Contract only | [Long-running MCP operations in Foundry](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol#long-running-operations-preview) |

Tools return data, never instructions. All calls emit start, decision, completion, and failure events with redacted summaries.

## Cache Patterns

| ID | Pattern | Required behavior | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="cache-01"></a>CACHE-01 | Exact-key model cache | Scope by tenant, user authorization, model, prompt, and policy versions | Contract only | [Prompt caching in Azure OpenAI](https://learn.microsoft.com/azure/foundry/openai/how-to/prompt-caching) |
| <a id="cache-02"></a>CACHE-02 | Retrieval artifact cache | Cache immutable, versioned retrieval results with freshness metadata | v1.0 | [Cache-Aside pattern](https://learn.microsoft.com/azure/architecture/patterns/cache-aside) |
| <a id="cache-03"></a>CACHE-03 | Safe tool-result cache (new) | Cache read-only results with authorization-aware keys and TTL | v1.0 | No external source; design document required |
| <a id="cache-04"></a>CACHE-04 | Semantic response cache | Match similar requests while preserving policy and evidence validity | Deferred | [Semantic cache with Azure Cosmos DB](https://learn.microsoft.com/azure/cosmos-db/gen-ai/semantic-cache) |

Cache keys must include all authorization and freshness dimensions that affect validity. Consequential actions, approval decisions, and mutable results without a reliable version are never cached.

## Evaluation Patterns

| ID | Pattern | Evidence | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="eval-01"></a>EVAL-01 | Deterministic policy evaluation | Pass/fail tests for controls and routes | v1.0 | [Agent Framework evaluation](https://learn.microsoft.com/agent-framework/agents/evaluation) |
| <a id="eval-02"></a>EVAL-02 | Task outcome evaluation | Goal completion and task adherence | v1.0 | [Foundry agent evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/agent-evaluators) |
| <a id="eval-03"></a>EVAL-03 | Grounding and answerability | Citation quality, evidence support, abstention behavior | v1.0 | [Foundry built-in evaluation metrics](https://learn.microsoft.com/azure/foundry/how-to/evaluate-results#understand-the-built-in-evaluation-metrics) |
| <a id="eval-04"></a>EVAL-04 | Tool-call evaluation | Tool choice, arguments, ordering, and result use | v1.0 | [Foundry agent evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/agent-evaluators) |
| <a id="eval-05"></a>EVAL-05 | Safety evaluation | Direct and indirect attack resistance and policy compliance | v1.0 | [Foundry safety evaluators](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/risk-safety-evaluators) |
| <a id="eval-06"></a>EVAL-06 | Performance evaluation | Latency, token usage, and estimated cost | v1.0 | [Observability in generative AI](https://learn.microsoft.com/azure/foundry/concepts/observability) |
| <a id="eval-07"></a>EVAL-07 | Regression comparison | Candidate versus approved baseline and thresholds | v1.0 | [Evaluation datasets in Foundry](https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-datasets) |
| <a id="eval-08"></a>EVAL-08 | Production trace sampling | Curate approved traces into versioned datasets | v1.0 | [Evaluation datasets in Foundry](https://learn.microsoft.com/azure/foundry/observability/how-to/evaluation-datasets) |

Pull requests run deterministic tests and a small stable evaluation smoke set. Broader Foundry evaluations run on schedule and before release. Dataset, prompt, model, policy, evaluator, and result versions must remain traceable.

## Observability and Audit Patterns

| ID | Pattern | Purpose | v1.0 disposition | Reference |
|---|---|---|---|---|
| <a id="obs-01"></a>OBS-01 | Distributed tracing | Correlate API, model, retrieval, tool, state, and evaluation spans | v1.0 | [Agent Framework observability](https://learn.microsoft.com/agent-framework/agents/observability) |
| <a id="obs-02"></a>OBS-02 | Operational metrics | Errors, latency, saturation, retries, and availability | v1.0 | [Application Insights overview](https://learn.microsoft.com/azure/azure-monitor/app/app-insights-overview) |
| <a id="obs-03"></a>OBS-03 | Outcome metrics (new) | Goal, grounding, answerability, tools, safety, latency, and cost | v1.0 | No external source; design document required |
| <a id="obs-04"></a>OBS-04 | Append-only audit events | Record security and policy-relevant transitions | v1.0, mandatory | [Azure security logging and threat detection](https://learn.microsoft.com/security/benchmark/azure/mcsb-v2-logging-threat-detection) |
| <a id="obs-05"></a>OBS-05 | Payload-controlled diagnostics | Capture identifiers, hashes, counts, timings, and redacted summaries | v1.0, mandatory default | [Agent tracing security and privacy](https://learn.microsoft.com/azure/foundry/observability/concepts/trace-agent-concept#security-and-privacy) |

Operational health and agent-quality health remain separate scorecards linked by run ID. Audit persistence is independent from diagnostic sampling and trace retention.

## Configuration and Registration

Profiles select registered implementations and thresholds; they cannot import arbitrary code or disable mandatory controls.

```yaml
schemaVersion: 1
profile: grounded-assistant
memory:
  working:
    adapter: in-memory
  conversation:
    adapter: cosmos-session
    ttlHours: 24
  episodic:
    adapter: azure-ai-search
    writePolicy: significant-events
state:
  adapter: cosmos-run-state
context:
  budgetPolicy: bounded
  compaction: structured-summary
retrieval:
  adapter: azure-ai-search
  strategy: hybrid
answerability:
  onInsufficientEvidence: clarify
governance:
  maximumTier: propose
```

The composition root maps each adapter name to an installed implementation. Schema validation rejects unknown adapters, incompatible combinations, missing mandatory controls, and unsafe limits before the runtime starts.

## Conformance Test Families

| Test family | Applies to | Minimum proof |
|---|---|---|
| Identity scope | Memory, state, retrieval, cache, tools, audit | No cross-tenant or cross-user access |
| Lifecycle | Memory, state, artifacts, cache | TTL, retention, deletion, and expiry behavior |
| Concurrency | State, approval, idempotency | Stale-write rejection and replay safety |
| Trust boundary | Context, retrieval, memory, tools | Untrusted data cannot become instructions |
| Error semantics | All adapters | Timeouts, retries, cancellation, and normalized errors |
| Auditability | Policy, tools, approval, state | Correlated append-only events for every transition |
| Substitutability | Every port | In-memory and Azure implementations pass the same contract |

## Incremental Delivery Use

For each implementation increment:

1. Reference one or more pattern IDs in the GitHub issue.
2. Identify the owning contract and mandatory semantics.
3. Add or update the conformance test before the adapter implementation.
4. Implement the smallest runnable path using registered adapters.
5. Record architecture changes in an ADR when semantics or ownership boundaries change.
6. Attach validation evidence and update the phase status in the implementation plan.

A pattern is complete only when its contract, reference implementation, configuration example, tests, security behavior, telemetry behavior, and operating guidance agree.
