# Production Agent Harness Blueprint

**VBD Proposal: Harness Engineering Agenda, Cookie-Cutter Template and Customer Story**

- **Proposer:** Arunkumar Gopalan, Sr Cloud Solution Architect, UK CSU C AI
- **Sponsor sought:** Prometheus IP. Built on Foundry evaluation and tracing; AgentOps Accelerator integration is optional.
- **Date:** 10 September 2026. **Status:** draft for review. Industry-agnostic: no customer-specific content or references.


## Part 1: Presentation and Storytelling

This part is the customer-facing narrative. It explains the problem, introduces the Agent Harness without leading with product architecture, demonstrates the governed template, and closes with measurable proof and a clear sponsorship ask.

### 1.1 Positioning

Microsoft Foundry provides the platform to build and operate agents. Production-readiness guidance defines what good looks like. The Agent Harness turns that guidance into a reusable, governed implementation by standardising runtime controls for context, tools, memory, state and approvals. Its companion Evaluation Harness proves that the agent meets quality, safety, performance and cost thresholds before and after release.

### 1.2 Why an Agent Harness

Customers face several Microsoft products at different layers, not three interchangeable harnesses. MAF provides the reference agent runtime and released Harness composition; Foundry provides models, hosted execution, evaluation and tracing; GitHub Copilot SDK may serve distinct Copilot-centric scenarios but is not on the v1.0 critical path. There is no owned CSU guidance that joins these layers into a governed production template. Teams that start from a blank repository still rebuild policy, persistence, tool registration and an Evaluation Harness, and get them wrong in the same ways.

The Prometheus Q3 FY26 update lists the AgentOps Toolkit, Factory Agent Forge, GPT-RAG, ContentFlow and AI Landing Zones. None of them gives a customer a harness to clone. The choose, build and govern stage is unowned, and it is where most production agent failures originate. This VBD fills that stage using Foundry product capabilities directly for evaluation and operations, so it stands on its own and has no dependency on another CSU accelerator.

### 1.3 Customer Story: Presentation Flow

Three acts, eleven slides. The story moves from why the harness decides the outcome, to how to build one that can be trusted, to proof it is ready. The template is the protagonist: it appears on slide 5 and stays on screen for every slide after that. Use one idea per slide, two live demos, no architecture diagram before slide 4, and no product name before the customer's problem has been stated in their own words. The presentation runs for forty minutes with demos or twenty minutes without them.

| Act | Slide | Title | Story beat and what is on screen |
|---|---|---|---|
| 1. The harness decides the outcome | 1 | What an agent really is | A model plus a loop. The loop owns tools, context, memory and permissions; the model reasons. Set up the problem in the customer's own words. |
| | 2 | Same model, three behaviours | One model, three harness profiles, three different results on the same task. The harness, not the model, explains the difference. |
| | 3 | Where production agents fail | Runaway tool calls, context overflow, data leaking into memory and actions nobody approved. These are harness failures, not model failures. |
| 2. Build one you can trust | 4 | Runtime, platform and extension ports | MAF Harness, Foundry services and customer-replaceable adapters on one slide. This is the first architecture diagram. |
| | 5 | Start from a template, not a blank repository | Demo 1: clone, configure and run a governed agent. |
| | 6 | Governance in the loop | Show permission tiers and human approval gates as configuration, not as a conceptual diagram. |
| | 7 | Memory with boundaries | Show what the agent remembers, what it may never remember, and how code enforces the rule. |
| 3. Prove it is ready | 8 | Demo 2: a gated action | The agent proposes an action, the gate holds it, a human approves it, and the audit trail records each transition. |
| | 9 | Measured, not hoped | Run the Evaluation Harness and show the Foundry baseline, trace view and release thresholds. |
| | 10 | From clone to production | Show the dev, test and production path, including the owner and evidence required at each promotion gate. |
| | 11 | Your first thirty days | Show what the customer's team does next week with the template and what the VBD workshop adds. |

The presenter replaces the sample scenario with the customer's use case and leaves the governance and Evaluation Harness unchanged.

### 1.4 Talk Track by Audience

| Audience | Open with | Close with |
|---|---|---|
| Executive sponsor | The cost of an agent that fails in production; the harness is the control surface. | A governed agent in days, with an evaluation report as the go-live artefact. |
| Platform or AI lead | A stable governance contract with replaceable infrastructure ports. | A repeatable path for every agent team in the organisation. |
| Security and risk | Permission tiers, approval gates, audit trail and data boundaries, all as code. | Nothing acts without policy; every action is traceable. |
| Engineering team | Clone and run in under an hour. | Replace the sample tools with yours; retain governance and evaluation. |

### 1.5 The Ask

Prometheus sponsorship for the Production Agent Harness Blueprint as a Unified VBD, one named reviewer for the four-week build, and agreement that the template repository is published under the Azure GitHub organisation. If approved, the build starts the following Monday and template v1.0 is available for its first field delivery within four weeks.

## Part 2: Build the Agent Harness Template

This part is the engineering specification. It defines what the repository ships, what customers may configure, what controls remain mandatory, how the companion Evaluation Harness works, and how the template reaches a repeatable Azure deployment.

### 2.1 Review Decisions Incorporated

The candidate ideas have been resolved into the product scope rather than kept as a separate backlog. The detailed engineering review is in [`../docs/blueprint-review.md`](../docs/blueprint-review.md).

| Candidate idea | Decision in this proposal |
|---|---|
| Two production monitors | Adopt. Service health (availability, errors, latency, throttling and dependencies) is measured separately from agent outcome quality (goal completion, answerability, tool correctness, safety and cost), correlated by run ID. |
| Answerability gate | Adopt for grounded profiles. Before answering, the harness routes weak evidence to retrieve again, clarify, caveat, abstain or escalate. |
| Harness staleness | Adopt as dependency and configuration freshness: SDKs, model deployments, prompts, indexes, policies and evaluation baselines are versioned and checked in CI. |
| Faster inference | Treat as an optimization profile after measurement, using context reduction, safe caching, model routing and bounded concurrency. It is not a new harness primitive. |
| Caching | Adopt immutable tool-result and retrieval-artifact caching first. Semantic response caching is deferred and must key on tenant/user authorization, policy, model and freshness. Acting-tool calls are never cached. |
| MAF Harness | Adopt as the v1.0 runtime. The template composes the released MAF Harness rather than implementing a parallel loop. Foundry remains the reference model, hosting, evaluation and tracing platform. |

Primary references: [MAF Agent Harness](https://learn.microsoft.com/agent-framework/concepts/harness), [MAF getting started](https://learn.microsoft.com/agent-framework/get-started/) and [Microsoft Foundry samples](https://github.com/microsoft-foundry/foundry-samples).

### 2.2 Harness Engineering Agenda

The VBD teaches harness engineering as a discipline: the loop around the model that decides what an agent can see, call, remember and do without asking. Seven modules, in delivery order. Every module ends with a concrete output that lands in the customer's cloned template, so the agenda and the template are the same artefact viewed two ways.

| Module | Topic | What is covered | Output in the template |
|---|---|---|---|
| 1 | Harness fundamentals | What a harness is: the loop around the model that owns planning, tool calls, context, memory and permissions, and why it shapes agent behaviour more than the model choice does. | Shared vocabulary; harness responsibility map |
| 2 | Runtime and platform selection | MAF Harness as the reference runtime, Foundry as the reference platform, and the adapter contract required for any future framework. | Runtime and hosting decisions recorded in the template decision log |
| 3 | Agent loop and context engineering | MAF Harness composition, stop conditions, prompt layering, context budgeting, compaction, retrieval and answerability routing. | Configured loop, prompt layers and answerability policy |
| 4 | Tools and MCP | Tool contracts and schemas, MCP servers and clients, allow-listing, tool result handling, error and retry policy. | Tool registry and MCP allow-list |
| 5 | Memory and state | Session state versus long-term memory, data boundaries, what must never enter memory, retention. | Memory policy and storage adapter wired |
| 6 | Governance and human-in-the-loop | Identity, permission tiers, approval gates before consequential actions, audit trail, prompt-injection handling. | Governance config and HITL gates active |
| 7 | Evaluation Harness and operations hand-off | Building the companion Evaluation Harness on Foundry evaluation and tracing: versioned datasets, evaluators, CI baselines, release thresholds, red-team scans, service-health telemetry and outcome-quality monitoring. AgentOps Accelerator adapter is optional. | Evaluation Harness running; baseline recorded; both monitoring planes active |

Sequence matters. Modules 1 to 3 settle the design decisions, modules 4 to 6 build and govern the harness, and module 7 proves it through the Evaluation Harness so the customer leaves with something measurable, not a demo. The two-day workshop in section 2.6 is this agenda with hands-on labs against the template; a half-day executive version uses modules 1, 2 and 6 only.

The Evaluation Harness is a companion test and monitoring layer, not part of every live request. It invokes the same production Agent Harness entry point against versioned datasets, captures responses and traces, applies Foundry and deterministic evaluators, compares results with approved baselines, and enforces release thresholds. Production samples can be evaluated asynchronously and promoted into regression datasets.

### 2.3 Scope

| Boundary | Detail |
|---|---|
| In scope | Harness engineering curriculum (seven modules), a cloneable MAF-based Agent Harness Template, a companion Evaluation Harness, typed extension ports, a runtime and platform selection guide, a governance pack and a customer story deck. Evaluation and tracing use Foundry and Application Insights directly. Industry-agnostic throughout. |
| Out of scope | A new harness framework or runtime; configuration-only switching between incompatible frameworks; a new evaluation or observability layer; deploying an enterprise landing zone; Copilot Studio-only scenarios; building any customer's specific use case. The AgentOps Accelerator remains an optional adapter, not a dependency. |
| Who it is for | Any enterprise engineering team with a Foundry project and an agent use case to take to production. No industry or customer assumptions: the customer's use case plugs into the template's sample tool set. |

### 2.4 Deliverables

Six assets. The Agent Harness Template is the centre; the Evaluation Harness proves its behaviour. The agenda teaches both, the selection guide justifies the choices inside them, the governance pack is their policy configuration, and the story deck sells the outcome. Format follows the existing Agentic App Modernization L300 VBD so the package slots into the CSU Technical Success Solutions Catalog without a new template.

| # | Deliverable | What the customer receives |
|---|---|---|
| 1 | Agent Harness Template | Cloneable repository with a production-oriented MAF Harness, typed adapter contracts, sample tools, governance, tracing, and equivalent `azd up` and **Deploy to Azure** paths backed by canonical Bicep. |
| 2 | Evaluation Harness | Companion runner using the production Agent Harness entry point, with versioned datasets, deterministic and Foundry evaluators, baseline comparison, release thresholds, CI integration, trace-to-regression workflow and production quality monitoring. |
| 3 | Harness Engineering Workshop Kit | Two-day L300 delivery of the seven modules in section 2.2, with facilitator notes and hands-on labs performed against both harnesses. |
| 4 | Runtime and Platform Selection Guide | Layered decision matrix for MAF Harness, Foundry hosting/evaluation, and future runtime adapters, including the conformance evidence required before adding another framework. |
| 5 | Governance Pack | MCP and tool allow-list schema, permission tiers, human-in-the-loop gate patterns, audit and data-boundary rules, all shipped as files inside the template. |
| 6 | Customer Story Deck | Eleven-slide storytelling presentation from Part 1, with two demo scripts and audience-specific talk tracks. |

### 2.5 Agent Harness Template

A cloneable repository a customer runs in under an hour and owns from the first commit. It is scaffolding, not a framework: every abstraction in it already exists in the underlying SDK, the sample tools are meant to be deleted, and the governance and Evaluation Harness are meant to stay. The clone-to-run path below is the first hands-on lab of the workshop and the first live demo in the story deck.

#### 2.5.1 Clone-to-run Path

| Step | What the customer does | Time | Result |
|---|---|---|---|
| Clone | Clone the template repository and select an assistant, grounded or action profile (5.2). | 5 min | Profile and Azure Dev configuration selected |
| Configure and deploy | Select their Foundry project and governance tier, then use `azd up` or **Deploy to Azure**. | 30 min | Azure Dev is running under their identity and policy |
| Run and evaluate | Run the sample scenario, then run the first Foundry evaluation against the golden dataset to capture a baseline. | 20 min | Evidence the harness works and is measurable |
| Extend | Replace sample tools with their own, register MCP servers, tune prompts and memory policy. | Days, not weeks | Their agent, on a governed and evaluable harness |

#### 2.5.2 Runtime and Profiles

Version 1.0 ships one runtime adapter: the released Python MAF Harness, using Foundry as the reference model, hosting, evaluation and tracing platform. Customer variation happens through validated profiles and typed ports for model clients, retrieval and chunking, context policy, memory, durable state, approval, telemetry and evaluation. A future framework requires a code adapter and must pass the shared conformance tests; it is not enabled by changing one string.

Azure Dev is the supported daily development environment: it hosts the harness/API, Foundry inference and evaluation, Application Insights, retrieval, persistent state, cache and deployed tools. `azd up` and **Deploy to Azure** produce the same topology from canonical Bicep. Local use is limited to source editing, deterministic tests, in-memory adapters and optional debugging against Azure Dev; it does not host model inference.

| Profile | Best for | Enabled policy |
|---|---|---|
| Assistant | Single-agent tasks with tools and bounded execution | Session state, context budget, typed tools, audit and service/outcome telemetry |
| Grounded assistant | Evidence-based answers over customer data | Assistant controls plus retrieval, citations, freshness and answerability routing |
| Action agent | Tasks that can change an external system | Assistant controls plus propose/act split, durable checkpoints, approval and idempotency |

Multi-agent orchestration is deferred until after v1.0 and until the single-agent contracts and operations baseline have passed two customer pilots.

#### 2.5.3 Repository Layout

The repository boundaries map onto the agenda modules, so a workshop participant always knows where the current module lands in code or configuration. Secrets never live in the repository; identity is Entra-based throughout, the Azure dev topology deploys with azd/Bicep, and production overlays integrate with the customer's landing zone.

| Folder | Contents |
|---|---|
| `src/core/` | Framework-neutral policy models, normalized events, typed ports and conformance contracts |
| `src/adapters/` | MAF runtime, Foundry, retrieval, memory, durable state, approval, telemetry and storage adapters |
| `src/app/`, `config/` | Application entry points, dependency composition, validated profiles and environment-safe examples |
| `evals/`, `tests/` | Evaluation Harness runners, versioned datasets, evaluator and threshold configuration, result contracts, policy and adapter tests, and deployment smoke tests |
| `infra/`, `docs/` | azd/Bicep deployment, landing-zone integration guidance, architecture, ADRs, workshop material and operations runbooks |

#### 2.5.4 Template Components

Ten components. The third column is what ships and works on clone; the fourth is what the customer is expected to change. Anything not in the fourth column is deliberately hard to change, because it is the governance the template exists to carry.

| Component | Purpose | Ships in the template | Customer customises |
|---|---|---|---|
| Agent loop | Plan, act, observe cycle | MAF Harness composition with bounded execution | Stop conditions and limits within mandatory ceilings |
| Prompt layers | System, instructions, skills | Layered prompt files with a defined override order | Domain instructions and skills |
| Context manager | Keep the window within budget | Budgeting and compaction policy | Thresholds; what to retain on compaction |
| Tool registry | Declare and validate tools | Schema, three sample tools, error and retry policy | Their own tools |
| MCP integration | Connect external capability | MCP client, allow-list, one sample server | Approved servers |
| Memory | Session and long-term state | Adapters with a default data-boundary rule | Storage backend, retention |
| Permission tiers | What the agent may do unasked | Read, propose and act tiers | Tier assignment per tool |
| Human-in-the-loop gates | Approval before consequential actions | Gate pattern and approval hook | Which actions gate; who approves |
| Audit and telemetry | Every action traceable | Structured events and OpenTelemetry traces to Application Insights | Retention, dashboards |
| Evaluation Harness | Prove quality and control release risk | Runner against the production Agent Harness entry point, versioned datasets, deterministic and Foundry evaluators, baseline comparison, CI thresholds, trace-to-regression workflow; optional AgentOps adapter off by default | Their datasets, evaluators, sampling policy and thresholds |

### 2.6 Workshop Agenda (L300, Two Days)

The seven modules delivered hands-on against the template. Day 1 ends with a running harness on the customer's Foundry project; day 2 ends with it governed and evaluable. Prework: a provisioned Foundry project, one use case the team can describe in a paragraph, and the customer's current security and data policies.

| Day | Time | Module and session | Lab output |
|---|---|---|---|
| 1 | 09:00 - 10:00 | Module 1: Harness fundamentals | Harness responsibility map for the customer's use case |
| 1 | 10:15 - 11:45 | Module 2: Runtime and platform selection | Runtime, hosting and profile decisions logged |
| 1 | 11:45 - 12:30 | Lab: clone-to-run | Template deployed to Azure Dev against sample tools |
| 1 | 13:30 - 15:00 | Module 3: Agent loop and context engineering | Prompt layers and context budget configured |
| 1 | 15:15 - 17:00 | Module 4: Tools and MCP | First customer tool registered; MCP allow-list drafted |
| 2 | 09:00 - 10:30 | Module 5: Memory and state | Memory policy and data-boundary rule set |
| 2 | 10:45 - 12:30 | Module 6: Governance and human-in-the-loop | Permission tiers assigned; one gated action working |
| 2 | 13:30 - 15:30 | Module 7: Evaluation Harness and operations hand-off | Evaluation Harness executed; Foundry baseline recorded; traces visible in Application Insights; red-team scan run |
| 2 | 15:45 - 17:00 | Thirty-day plan and readback | Backlog with owners; story deck rehearsed for the customer's sponsor |

### 2.7 Pattern Catalogue by Topic

Each agenda module teaches a small set of named patterns, and each pattern exists in the template as code or configuration the customer can point at. The tables below are the teaching content of the workshop and the design vocabulary of the story deck. The third column says where the pattern lives in the template and what the customer changes.

#### 2.7.1 Loop Patterns (Module 3)

| Pattern | Use when | In the template |
|---|---|---|
| Reason-act-observe loop | Default for single-agent work: the model decides the next step from the last observation. | Default loop in `harness/`. Customer sets step and token limits only. |
| Plan then execute | Multi-step tasks where the plan should be visible and approvable before any action runs. | Planner step enabled by configuration; plan is emitted as a reviewable artefact before execution. |
| Reflection and critique | Output quality matters more than latency: drafts, code, analysis. | Optional critic pass on the final answer with a maximum of one revision cycle. |
| Bounded loop with budget | Every production agent. Prevents runaway tool calls and cost. | Hard limits on steps, tokens and wall-clock time, plus explicit stop conditions. Not customer-removable. |
| Interrupt and resume | A step needs human approval or an external event before the loop can continue. | Loop checkpoints its state to the state store, pauses at the gate, resumes on approval. |

#### 2.7.2 Context Patterns (Module 3)

| Pattern | Use when | In the template |
|---|---|---|
| Layered prompts | Always. Separates what never changes (system) from what the customer owns (instructions) and what loads per task (skills). | Three prompt files with a fixed override order; the customer edits instructions and skills, not system. |
| Progressive disclosure | The agent has many skills or tools and cannot afford to carry all of them in every turn. | Skills and tool descriptions load on demand from a short index; full definitions only when selected. |
| Budget and compaction | Long sessions. The window fills with history that no longer matters. | Context budget per turn; older turns summarised when the threshold is crossed. Threshold is customer-tunable. |
| Result by handle | Tool results are large (documents, query output) and would swamp the window. | Large payloads are stored and a handle is passed to the model; the model requests slices as needed. |
| Just-in-time retrieval | Grounding data is large or changes often; loading it up front is wasteful or stale. | Retrieval is a tool the loop calls per step rather than a bulk pre-load. |
| Answerability gate | A response must be supported by retrieved evidence. | Scores evidence sufficiency and routes to answer, retrieve again, clarify, caveat, abstain or escalate. Enabled for grounded profiles. |
| Scoped artifact cache | Retrieval or immutable tool output is expensive and safely reusable. | Cache key includes tenant/user authorization, source and policy version, and freshness. Acting-tool calls are never cached. |

#### 2.7.3 Tool and MCP Patterns (Module 4)

| Pattern | Use when | In the template |
|---|---|---|
| Typed tool contract | Always. Inputs and outputs are schema-validated before and after the call. | Tool registry rejects any tool without a schema; three sample tools show the shape. |
| Allow-list registry | Always. The model can only call what is registered and permitted for its tier. | `governance/allowlist` defines tools and MCP servers; anything unlisted is invisible to the model. |
| One MCP server per domain | Connecting to several systems. Keeps blast radius and permissions per system. | Sample MCP server per capability; customer registers one server per system of record. |
| Propose and act split | Consequential actions: writes, payments, sends, deletes. | Each acting tool has a dry-run twin; the propose result is what the human approves at the gate. |
| Structured error return | Tool failures should inform the model, not crash the loop. | Errors return as typed results with a retry hint; retry with backoff is configured per tool. |
| Untrusted output isolation | Tool or MCP output may contain instructions (documents, web pages, emails). | Tool results are wrapped as data, never appended as instructions; injection checks run before the model sees them. |

#### 2.7.4 Memory Patterns (Module 5)

| Pattern | Use when | In the template |
|---|---|---|
| Working memory | Within one session. Turn history, scratch results, current plan. | In-process, discarded at session end. Nothing is persisted from it without passing the write gate. |
| Episodic memory | The agent should recall what happened in earlier sessions with this user or case. | Session summaries written to the memory store at session end; retrieved by user and case key. |
| Semantic memory | Stable facts and preferences the agent should know without being told again. | Key-value facts with a source and timestamp; retrieved by search, never loaded wholesale. |
| Memory write gate | Always. Decides what may be remembered. | Explicit criteria for writes; personal, secret and regulated data categories are blocked by default and the block is not customer-removable. |
| Scoped and expiring memory | Multi-user or multi-tenant agents; regulated data. | Every memory record carries an identity scope and a time to live; the adapter enforces both on read. |

#### 2.7.5 State Patterns (Module 5)

| Pattern | Use when | In the template |
|---|---|---|
| Externalised state | Any agent that must survive a restart, scale out, or pause for approval. | Run state lives in a state store, not in process memory; the loop reads and writes it each step. |
| Checkpoint and resume | Long or gated tasks. The loop must pick up exactly where it stopped. | Checkpoint at every step boundary and every gate; resume reconstructs the loop from the last checkpoint. |
| Append-only run log | Always. Audit, replay and debugging need the full sequence of decisions and actions. | Every plan, tool call, result and approval is an event with a correlation id; nothing is overwritten. |
| Idempotency keys | Acting tools may be retried after a failure or a resume. | Each acting call carries a key derived from run and step; the tool contract requires tools to honour it. |
| Action state machine | Consequential actions that pass through approval. | Proposed, approved, executed, recorded. Transitions are enforced in `governance/`, not in the tool. |

#### 2.7.6 Governance and Human-in-the-Loop Patterns (Module 6)

| Pattern | Use when | In the template |
|---|---|---|
| Permission tiers | Always. Defines what the agent may do without asking. | Read, propose and act tiers; every tool is assigned a tier; the run has a maximum tier set at start. |
| Approval gate | Before any act-tier call. | Gate holds the proposed action, notifies the approver, resumes or aborts on decision. Approver hook is customer-wired. |
| Policy as code | Governance must be reviewable, versioned and testable like any other code. | Allow-lists, tiers and data-boundary rules are files in `governance/` under source control with tests. |
| Least-privilege identity | Always. The agent acts as itself or on behalf of a user, never with a shared secret. | Managed identity per agent; on-behalf-of flow when acting for a user; no credentials in the repository. |
| Injection defence in depth | The agent reads content it did not author. | Untrusted content is labelled, instructions inside it are ignored, and any request to change recipients or targets is surfaced for confirmation. |
| Traceable audit | Always. Every action must answer who, what, why, when. | Run log events carry identity, tier, approval reference and correlation id, exported to Application Insights. |

#### 2.7.7 Orchestration Patterns (Module 2, Post-v1.0)

| Pattern | Use when | In the template |
|---|---|---|
| Single agent first | Default. Most use cases do not need more than one agent with good tools. | All v1.0 profiles. The selection guide requires a stated reason before moving to any pattern below. |
| Supervisor and specialists | Distinct sub-tasks need different tools, prompts or permissions. | Supervisor routes to specialist agents; each specialist has its own tier and allow-list; governance is shared. |
| Sequential hand-off | A fixed pipeline: gather, analyse, draft, review. | Ordered workflow with typed hand-off contracts between stages; each stage checkpointed. |
| Parallel fan-out and aggregate | Independent sub-tasks that can run at once, then need combining. | Fan-out with a bounded concurrency limit; aggregator step applies the same critique pattern as 8.1. |
| Shared governance layer | Always, in any multi-agent design. | One allow-list, one tier model, one run log across all agents; no specialist can exceed the run's maximum tier. |

#### 2.7.8 Evaluation Harness and Operations Patterns (Module 7)

The Evaluation Harness invokes the production Agent Harness through its supported entry point so test behavior matches deployed behavior. It uses Foundry evaluation, Foundry tracing and Application Insights as shipped. No CSU accelerator sits in the critical path. The AgentOps Accelerator appears only as the last row: an optional adapter, off by default, and the lowest build priority in the plan.

| Pattern | Use when | In the template |
|---|---|---|
| Golden dataset and baseline | From the first run. Nothing improves without a baseline to compare against. | Seed dataset in `evals/`; the first Foundry evaluation run is captured as the baseline for all later comparisons. |
| Evaluation gate in CI | Every change to prompts, tools or loop configuration. | Pull request workflow runs the Foundry evaluators and blocks on regression against the baseline. |
| Trace to regression | Production behaviour reveals a case the dataset did not cover. | Traces exported from Application Insights are promoted into the evaluation dataset so the failure becomes a permanent test. |
| Red-team before release | Any release that widens tools, permissions or audience. | Foundry red-teaming and content safety scans run as part of release readiness; results attached to the evaluation report. |
| Evaluation report as go-live artefact | Every promotion to production. | Baseline comparison, red-team results and tracing configuration produced as one reviewable report. VBD exit criterion. |
| Dual operational scorecards | Always in production. Green infrastructure does not prove that the agent completed the user's goal. | Application Insights reports service health; Foundry evaluation reports outcome quality. Both correlate on run and trace identifiers. |
| Freshness report | Every dependency or content release. | CI records SDK, model, prompt, policy, index and evaluation-baseline versions and flags stale or incompatible combinations. |
| Optional AgentOps adapter | The customer already runs the AgentOps Accelerator and wants the harness to appear in it. | A single configuration flag emits `agentops.yaml` and an evidence pack from the same `evals/` data. Off by default; built last, after v1.0, only if field demand exists. |

### 2.8 Implementation Approach and Product Dependencies

Weeks 1 to 4: build the v1.0 release candidate as one thin, cloud-first MAF Harness vertical slice with three profiles, sample tools, governance controls, Foundry evaluation and tracing, and azd/Bicep deployment. Weeks 5 to 9: run grounded and action-oriented field pilots and record what customers change. Weeks 10 to 11: harden and publish v1.0, finalise the evidence-based story deck, and submit the Unified VBD. Post-v1.0 work requires field demand: multi-agent orchestration, a second runtime adapter, semantic response caching and the optional AgentOps adapter.

The v1.0 critical path depends on released MAF Harness APIs, Microsoft Foundry, Foundry evaluation and tracing, Application Insights, azd and Bicep. Dependencies are pinned and update-tested. Experimental MAF capabilities remain behind feature flags with fallbacks. `azd up` and **Deploy to Azure** deploy equivalent Azure Dev environments; production overlays integrate with, but do not deploy, the customer's AI Landing Zone. Release requires a Foundry evaluation baseline, Application Insights traces, an outcome-quality scorecard, a passed red-team scan and clean-tenant deployment smoke tests for both paths.

### 2.9 Risks

Product churn is the main risk: the harness layer is where Microsoft is shipping fastest. The mitigation is that the template is thin scaffolding over released SDKs, pinned and re-tested on every release, with no abstraction of its own to maintain. The second risk is the template drifting into a framework; the rule is that nothing goes in that is not already in the underlying SDK, and the sample tools are deleted on day one of every engagement. The third is perceived overlap with Factory Agent Forge or the AgentOps Accelerator, handled by the week-1 boundary review, the explicit out-of-scope list in section 2.3, and by consuming Foundry directly so neither accelerator is in the critical path. The fourth is single ownership, handled by naming a co-owner after the first delivery and writing the workshop kit so any L300 CSA can run it.

