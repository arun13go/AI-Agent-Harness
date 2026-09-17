# Agent Harness Repository Instructions

## Mission

Build a reusable, production-oriented agent harness template that customers can clone, configure, deploy to their Azure tenant, and extend without replacing its governance and evaluation controls.

## Architecture Rules

- Use Microsoft Agent Framework (MAF) Harness as the first and reference runtime adapter. Compose released framework capabilities; do not create a competing agent runtime.
- Keep Microsoft Foundry as the reference model, hosting, evaluation, and tracing platform. Isolate Foundry-specific code behind explicit adapters where a stable product API permits substitution.
- Make these policy areas configurable through typed interfaces and validated configuration: model client, retrieval and chunking, context compaction, memory, durable run state, cache, tool registry, approval, telemetry export, and evaluation datasets.
- Do not promise that a user can switch agent frameworks with one configuration value. A new framework requires an adapter that passes the shared contract and conformance tests.
- Keep policy separate from mechanism. Customer-editable YAML selects registered implementations and thresholds; it must not import arbitrary code or bypass mandatory controls.
- Enforce bounded execution, least privilege, typed tool contracts, idempotency for acting tools, approval before consequential actions, tenant and user scoping, and append-only audit events.
- Treat retrieved content and tool output as untrusted data. Do not concatenate it into system or developer instructions.
- Keep business data out of telemetry by default. Record identifiers, hashes, counts, timings, decisions, and redacted summaries unless explicit policy allows payload capture.

## Repository Shape

- `src/core/`: framework-neutral contracts, policy models, domain events, and conformance tests.
- `src/adapters/maf/`: MAF Harness composition and MAF-specific providers or middleware.
- `src/adapters/foundry/`: Foundry model, hosted-agent, evaluation, and tracing integration.
- `src/adapters/storage/`: memory, checkpoint, artifact, and audit persistence implementations.
- `src/app/`: API or worker entry points and dependency composition.
- `config/`: versioned schemas and environment-safe example configuration.
- `evals/`: seed datasets, evaluator configuration, thresholds, and regression fixtures.
- `infra/`: azd and Bicep assets, including role assignments and observability resources.
- `tests/`: unit, contract, policy, integration, evaluation smoke, and deployment smoke tests.
- `docs/decisions/`: architecture decision records (ADRs).

Create folders only when the first real artifact for that boundary is added. Avoid empty scaffolding.

## Delivery Workflow

1. Read `README.md`, `docs/blueprint-review.md`, `docs/reference-architecture.md`, and `docs/implementation-plan.md` before changing architecture or scope.
2. State one local hypothesis and one check that can falsify it before the first edit.
3. Prefer a thin vertical slice over broad scaffolding. The first slice must deploy to Azure Dev through `azd up` and **Deploy to Azure**; deterministic policy and contract tests must run locally without Azure.
4. After the first substantive edit, run the narrowest relevant test, type check, schema validation, or link check before making further edits.
5. Record architecture changes in `docs/decisions/` using the next sequential ADR number. Keep implementation work logs under the ignored `.local/logs/` directory.
6. Pin released dependencies, automate update checks, and keep experimental MAF capabilities behind feature flags with fallback behavior.
7. Never commit secrets, tenant identifiers, subscription identifiers, connection strings, generated credentials, or local `.azure/` state.

## Deployment Contract

- Support both `azd up` and a **Deploy to Azure** button. The button uses an ARM artifact generated from the same canonical Bicep; CI must reject drift between the two paths.
- Both paths must preserve equivalent parameters, RBAC, outputs and post-deployment smoke tests.
- Use passwordless Entra authentication and managed identities. Optional local debugging against Azure Dev uses `DefaultAzureCredential` or the SDK-recommended equivalent.
- Deployment must be repeatable into a new subscription or resource group and expose all required outputs through azd environment values.
- Provide `dev` defaults with explicit production overlays. Production guidance must cover private networking, customer-managed storage choices, diagnostic retention, and least-privilege RBAC.

## Definition of Done

- Local deterministic tests and the Azure Dev quickstart both work from a clean clone.
- Mandatory governance controls cannot be disabled by ordinary application configuration.
- Contract tests prove each adapter preserves identity scope, approvals, audit events, and error semantics.
- Evaluation covers task outcome, groundedness or answerability where applicable, tool-call correctness, safety, latency, and cost.
- Operational health and agent-quality health are visible as separate signals.
- Documentation, architecture diagrams, ADRs, and validation evidence match the shipped implementation.

If you are in VS Code, read the vscode-microsoft-foundry skill first.