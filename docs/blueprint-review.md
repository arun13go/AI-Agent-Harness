# Blueprint Engineering Review

**Reviewed:** 13 September 2026  
**Source:** [Production Agent Harness Blueprint](source-blueprint.md)  
**Recommendation:** Proceed with one v1.0 reference runtime, a cloud-first Azure Dev path, and portability through adapter contracts.

## Executive Decision

The proposal's core thesis is sound: production behavior is governed by the loop around the model, and customers need reusable controls for tools, context, memory, state, approvals, telemetry, and evaluation. The pattern catalogue and workshop-to-template mapping should be retained.

The implementation model needs one material correction. Microsoft Agent Framework (MAF) now provides a released, opinionated Harness that already composes chat clients, function invocation, sessions, context providers, compaction, planning/todos, tool approvals, and OpenTelemetry. The template should configure and extend that harness rather than build another loop. Microsoft Foundry is the reference model, hosting, evaluation, and operations platform, not a peer harness framework. Other frameworks can be supported later through tested adapters.

## Findings

| Priority | Finding | Decision |
|---|---|---|
| Critical | The document presents Copilot SDK, MAF, and Foundry Agent Service as three equivalent harness choices. They operate at different layers. | Use MAF Harness as the v1.0 runtime. Use Foundry as the Azure platform. Defer any Copilot SDK runtime adapter until its target scenarios and contract parity are proven. |
| Critical | “Two flavours, switched by one configuration value” understates framework differences in sessions, tool invocation, streaming, approvals, and persistence. | Configuration may select an installed adapter. Adding or changing a framework is a code-level adapter change that must pass conformance tests. |
| High | The proposed template risks duplicating released MAF Harness capabilities. | Wrap only where a cross-cutting policy or replaceable infrastructure boundary exists. Do not reimplement the core agent loop, todo provider, modes, approvals, or compaction defaults without a measured gap. |
| High | “One click” is not yet an engineering contract. | Ship both `azd up` and **Deploy to Azure** in v1.0. Generate the portal template from the canonical Bicep and verify equivalent parameters, RBAC, outputs and smoke tests in CI. |
| High | Memory, conversation session, durable workflow state, artifact storage, and audit history are mixed together. | Give each a separate contract, retention policy, identity scope, and store. Never use chat history as the system of record for an action. |
| High | Evaluation focuses on model quality but not explicit business outcomes. | Use two operational scorecards: service health and agent outcome quality. Include goal completion, tool correctness, answerability, policy compliance, latency, and cost. |
| High | The answerability gate is absent from the main pattern catalogue. | Add it as a policy stage for grounded profiles. Its routes are answer, retrieve again, clarify, caveat, abstain, or escalate. |
| Medium | “AI Landing Zones deployment” is too broad for a clone-to-run promise. | Ship a self-contained dev topology and document integration points for an enterprise landing zone. Do not attempt to deploy an enterprise landing zone from this repo. |
| Medium | Some proposed capabilities are experimental in MAF. | Feature-flag background agents, file access, looping, and pre-release shell tooling. Maintain a fallback and do not make them required for v1.0. |
| Medium | “Nothing goes in that is not in the underlying SDK” conflicts with governance policy and adapters that the SDK does not own. | Permit thin domain contracts and policy middleware. Reject only new orchestration semantics that duplicate the framework. |

## Disposition of “New Idea - TBD”

| Idea | Disposition | Where it belongs |
|---|---|---|
| Dual production monitoring | Adopt for v1.0. Separate infrastructure health from agent outcome quality and correlate them by run ID. | Architecture, evaluation, dashboards, workshop module 7 |
| Answerability gate | Adopt for grounded/RAG profiles. It is not mandatory for agents that do not produce evidence-based answers. | Context policy, retrieval, evaluation dataset, module 3 |
| Harness staleness / MLADS | Reframe as dependency and configuration freshness. Track SDK versions, model deployments, prompts, indexes, policies, and eval baselines. | CI maintenance lane and release report |
| Accelerate inference with LLM | Reframe as a performance profile, not a harness primitive. Start with measurement, then model routing, prompt/context reduction, caching, and concurrency where safe. | Post-v1.0 optimization backlog |
| Cache harness | Adopt narrowly. Cache immutable tool results and retrieval artifacts first. Response or semantic caching requires tenant/user scope, policy version, model version, freshness, and authorization in the key. Never cache consequential actions. | Storage adapter and performance profile |
| MAF Harness reference | Adopt as the primary runtime baseline. | Runtime adapter and quickstart |

## Recommended Product Boundary

**Stable template core:** configuration schema, policy decisions, normalized run events, adapter contracts, conformance tests, evaluation assets, and deployment conventions.

**MAF-owned behavior:** agent execution, tool invocation pipeline, sessions, context providers, built-in compaction, planning/todos, modes, approvals, and OpenTelemetry integration.

**Foundry-owned behavior:** model deployment, hosted-agent lifecycle where selected, evaluation services, continuous evaluation, trace analysis, and project-level operations.

**Customer-owned behavior:** domain prompts, tools, MCP servers, data sources, chunking implementation, memory retention, approval integration, thresholds, and production topology overlays.

## v1.0 Scope

Ship one cloud-first Python reference implementation using released MAF Harness APIs and Foundry. Azure Dev hosts the harness and integration services; local use is limited to editing, deterministic tests, in-memory adapters and optional debugging. Include three safe sample tools, an approval-gated action, grounded answerability, OpenTelemetry, a seed evaluation suite and equivalent `azd up` and **Deploy to Azure** paths.

Do not put multi-framework switching, multi-agent orchestration, semantic response caching, an AgentOps adapter, or experimental MAF features on the v1.0 critical path.

## Acceptance Evidence

- A clean clone passes local deterministic tests and deploys Azure Dev with documented prerequisites.
- `azd up` and **Deploy to Azure** produce equivalent environments in clean resource groups.
- The same behavior contract passes against in-memory and Azure-backed state adapters.
- A write-like sample action cannot execute without approval and remains idempotent after retry.
- A weak-evidence query follows a configured answerability route.
- Application Insights shows service traces; Foundry evaluation reports outcome quality.
- CI blocks policy bypasses and measured evaluation regressions.

## Primary References

- [MAF Agent Harness](https://learn.microsoft.com/agent-framework/concepts/harness)
- [MAF getting started](https://learn.microsoft.com/agent-framework/get-started/)
- [Microsoft Foundry samples](https://github.com/microsoft-foundry/foundry-samples)

## Future Copilot Agent Expansion (v2/v3)

Copilot agents should be an explicit future product track, but they should not be presented as one interchangeable runtime. Microsoft 365 Copilot agents, Copilot Studio agents, and GitHub Copilot agents have different hosts, identity models, lifecycle controls, tool surfaces, and deployment contracts. Version 1.0 therefore remains the MAF Harness and Foundry reference implementation while the shared core preserves the policy and adapter boundaries needed for later integration.

### Candidate Surfaces

| Surface | Potential harness role | Initial disposition |
|---|---|---|
| Microsoft 365 Copilot agents | Make governed business agents available in Microsoft 365 and use Microsoft 365 knowledge, actions, and user context | Primary v2 discovery and prototype target |
| Microsoft 365 Agents SDK | Provide a pro-code host or channel adapter for Microsoft 365 and other supported channels | Evaluate in v2 against the runtime and channel contracts |
| Copilot Studio agents | Integrate low-code agents or workflows where platform-native governance and lifecycle remain authoritative | Integration pattern only; do not wrap or replace Copilot Studio orchestration |
| GitHub Copilot agents | Apply selected harness governance and evaluation assets to software-development agents and repository workflows | Separate coding-agent track; evaluate only when a concrete field scenario exists |

### Version 2: Discovery and Conformance Prototype

- Select one Microsoft 365 Copilot use case and document why Copilot is the required user experience or data boundary.
- Map channel activity, conversation state, user identity, tools, approvals, audit, telemetry, and evaluation to the existing harness ports.
- Build a read-only or propose-only prototype before enabling consequential actions.
- Prove tenant and user isolation, authorization at every downstream call, untrusted-content handling, bounded execution, and audit correlation.
- Document capability gaps and decide whether the result is a runtime adapter, channel adapter, tool integration, or separate deployment profile. Do not assume configuration-only switching.

### Version 3: Optional Productization

Productize a Copilot adapter or profile only if the v2 prototype passes the shared conformance tests and field demand justifies maintaining it. A v3 implementation must preserve mandatory harness controls, define its own deployment and lifecycle contract, support Copilot-native approval and administration experiences where available, and produce evaluation evidence comparable to the v1 reference path. Platform-owned policy remains authoritative when it is stricter than harness policy.

The expansion is complete only when identity scope, tool authorization, approval semantics, idempotency, normalized audit events, error behavior, telemetry privacy, and evaluation can be demonstrated end to end. Until then, Copilot support remains a roadmap item rather than a compatibility claim.

References:

- [Extend Microsoft 365 Copilot](https://learn.microsoft.com/microsoft-365-copilot/extensibility/overview)
- [Microsoft 365 Agents SDK](https://learn.microsoft.com/microsoft-365/agents-sdk/)
- [Architecting agent solutions for Microsoft 365 Copilot](https://learn.microsoft.com/agents/architecture/)
- [About GitHub Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent)
