# Azure Dev: Foundry Hosted Agent Container

## Current scope

The deployment target is **Microsoft Foundry Hosted Agents**, not Container Apps or AKS.
Container deployment preserves the repository's tested Python 3.12 runtime. Foundry owns the
agent endpoint, agent identity, and compute lifecycle; ACR stores the application image.

The [Dockerfile](../../Dockerfile) uses Python 3.12.13 and uv 0.11.15, installs frozen production
dependencies non-editably, checks the executable imports, runs as UID 10001, and starts
`python -m agent_harness.app.agent_http_server --hosted` on port 8088. Its health check calls
`/readiness` without invoking a model. The [build context](../../.dockerignore) excludes local
state and includes only the application, assistant configuration, schema, and packaging files.

The [azd manifest](../../azure.yaml) selects Responses protocol 2.0 and remote ACR build.
The [canonical Bicep](../../infra/main.bicep) and [generated ARM](../../infra/main.json) currently
provision **registry prerequisites only**:

- A dedicated Basic ACR with admin credentials and anonymous pull disabled.
- Registry-scoped `AcrPull` for the existing Foundry project's managed identity.
- An Entra-authenticated ContainerRegistry connection on that project.

They do not create, change, or delete the existing Foundry account, project, or model. They do
not yet deploy the agent or provision monitoring. The application can export metadata-only spans
to an existing Application Insights resource. Consequently this is not yet the
complete `azd up` / Deploy to Azure parity proof required by Phase 1D.

The Foundry AVM pattern was considered but creates an account/project, which this increment must
preserve. Registry provisioning instead composes the pinned
[registry AVM 0.13.1](https://github.com/Azure/bicep-registry-modules/tree/avm/res/container-registry/registry/0.13.1/avm/res/container-registry/registry).
Basic ACR uses authenticated public endpoints; private networking requires an explicit production
overlay and an appropriate registry tier. The project identity's pull permission is for platform
image retrieval, not the hosted agent's runtime authorization.

## Configure a development environment

Use an existing project with a system-assigned managed identity, a healthy model deployment, and
permission to create a registry, a project connection, and registry-scoped role assignments.
Sign in to Azure CLI and azd yourself. Store customer-specific values only in ignored local state:

```powershell
azd env new harness-dev --subscription "<subscription-id>" --location "<region>" --no-prompt
azd env set AZURE_RESOURCE_GROUP "<existing-resource-group>"
azd env set AZURE_AI_ACCOUNT_NAME "<existing-account>"
azd env set AZURE_AI_PROJECT_NAME "<existing-project>"
azd env set AZURE_AI_PROJECT_ID "<existing-project-arm-id>"
azd env set AZURE_AI_PROJECT_ENDPOINT "https://<account>.services.ai.azure.com/api/projects/<project>"
azd env set FOUNDRY_PROJECT_ENDPOINT "https://<account>.services.ai.azure.com/api/projects/<project>"
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "<existing-deployment>"
azd env set AZURE_TENANT_ID "<tenant-id>"
```

Run these from the repository root. Environment creation does not deploy Azure resources.
The parameter file uses azd environment substitutions. The generated ARM accepts the corresponding
plain parameters directly for a subscription deployment. Use incremental mode and review what-if.
Do not use `azd down` on this shared-project environment: review and delete only specifically
identified harness-owned resources through an approved cleanup procedure.

## Identity gates before agent deployment

The platform must supply `FOUNDRY_HOSTING_ENVIRONMENT`, `FOUNDRY_AGENT_TENANT_ID`,
`x-agent-user-id`, and `x-agent-foundry-call-id`. Foundry endpoint RBAC authorizes callers;
the container maps opaque user keys to tenant-bound subjects rather than maintaining a user
allowlist. Existing inherited endpoint permissions remain effective. Remove obsolete
`HARNESS_ALLOWED_FOUNDRY_USERS` settings; their presence fails startup.
Missing configuration fails startup. Do not fake platform environment variables in deployment.

Hosted model calls forward only the current opaque call ID, preserve bearer authentication, and
reject missing context before network access. Credentials retain managed/workload identity
discovery, excluding client-secret and developer-login fallback providers. Live credential and
ingress behavior still require cloud evidence.

### Caller-enrollment discovery

Historical finding: the operator approved replacing this blocked enrollment approach with
endpoint RBAC on 21 October 2026. See [ADR 0013](../decisions/0013-foundry-rbac-ingress-authorization.md).

The [runtime contract](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-contract#platform-request-headers-container-protocol-200)
documents `x-agent-user-id` as a platform-generated, opaque per-user partition identifier.
The same user gets the same value across agents. The platform generates identity headers from
verified identity and drops caller-supplied headers outside its forwarding allowlist. The installed
Core SDK exposes the key through `get_request_context().user_id`; this reads an inbound request,
not an enrollment directory.

The official contract and [user-isolation guide](https://learn.microsoft.com/azure/foundry/agents/how-to/isolate-sessions-per-user)
inspected for this increment do not document a pre-invocation lookup API or a mapping from an
Entra object ID to this key. Consequently, enrollment for the current application allowlist
remains blocked; this finding does not establish that no supported mechanism exists.
Do not derive a key, log raw identifiers to discover it, or deploy an allow-all bootstrap.

Two related platform features do not resolve this lookup:

- [Foundry Agent Consumer](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions)
  authorizes endpoint invocation through RBAC. Replacing the application allowlist with
  platform RBAC would change ADR 0011 and requires an explicit design decision, including the
  effect of inherited roles.
- Delegated `x-ms-user-identity` identifies an application's represented end user and requires
  an explicit impersonation permission. It is not a documented lookup for `x-agent-user-id`,
  and no equality between the two identifiers is assumed.

Resolve this through a supported enrollment mechanism confirmed by the platform, or approve a
revised authorization design before deploying. Platform documentation is not live evidence:
spoof-resistant ingress and actual runtime credentials still need acceptance tests.

### RBAC replacement assessment

Project-scoped platform RBAC is a supported alternative for endpoint authorization, but is
**not equivalent to the explicit application user allowlist**. The
[Foundry RBAC reference](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry)
supports Foundry Agent Consumer at project scope, or agent scope when access should cover only
one agent. Its data action is
`Microsoft.CognitiveServices/accounts/AIServices/endpoints/interact/action`.
Project scope grants endpoint access across that project.

Read-only checks against the selected development environment confirmed local/key
authentication is disabled. They also found inherited Foundry User assignments for both a user
and a service principal, plus a project-level Foundry User assignment. Foundry User includes
broad Cognitive Services data actions. Adding a narrower Consumer assignment does not remove
existing inherited access; a project-level assignment cannot fence out those principals.
This inspection is not an exhaustive effective-access proof for custom roles, groups, or
delegation.

A replacement design would therefore:

- Explicitly accept all callers authorized by effective platform endpoint RBAC, including
  inherited access, rather than promise a named-caller allowlist.
- Keep the authenticated Foundry ingress as the only entry path and retain strict validation
  of platform tenant configuration, user key, and per-request call ID.
- Keep deterministic tenant-bound hashed subjects, tool governance, approvals, and audit
  isolation. The configured deployment tenant is not proof of a caller's home tenant.
- Treat `x-client-*` identity claims as untrusted. Delegated end-user identity requires a
  separately reviewed trusted middle tier and impersonation permission.
- Require positive and negative live endpoint tests, forged-header tests, runtime credential
  proof, and an inherited-access review before declaring deployment acceptance.

**Assessment (subsequently approved):** viable if effective platform RBAC is the intended caller policy. It cannot
preserve the current named-user restriction in this shared environment without additional
supported authorization. No runtime policy or role assignments were changed by this assessment.
The operator approved this trade-off on 21 October 2026. ADR 0013 and the implementation now
replace the application allowlist while preserving strict platform context and subject isolation.

## Operational telemetry

The hosted entry point selects `application-insights` explicitly and requires
the platform-injected `APPLICATIONINSIGHTS_CONNECTION_STRING`. Reuse an approved existing
Application Insights resource; store the connection string only in ignored local azd state.
Do not commit it or put it in the Docker build context.
Foundry reserves that variable and all `FOUNDRY_*` / `AGENT_*` names: do not declare them in
the service's `environmentVariables`. The existing project monitoring connection and platform
provide them. Deployment acceptance must verify their actual injection, not fake them.

Only fixed operation names, trace/parent identifiers, timings, status codes, and numeric token
counts reach the exporter. Prompts, answers, tool payloads, raw caller identifiers, events,
links, arbitrary attributes, and exception details are dropped. Automatic SDK observability
and log export remain disabled. See [ADR 0014](../decisions/0014-metadata-only-application-insights-export.md).

## Validation and build

```powershell
uv run pytest
uv run pyright
uv run ruff check .
az bicep build --file infra\main.bicep --outfile infra\main.json
azd ai agent doctor --local-only --no-prompt
azd provision --preview --no-prompt
```

After the infrastructure plan and what-if are approved, `azd provision --no-prompt` creates the
registry prerequisites and records registry/project outputs in the active azd environment.
Remote builds do not require local Docker. Build source must be sent only to the approved
customer registry; never include ignored local state or credentials.

The caller-policy decision and safe telemetry composition are implemented. Validate local tests,
the selected environment, existing monitoring configuration, and the authenticated-ingress
acceptance plan before `azd deploy`. An image build alone is not a live deployment
or proof of governance. Keep readiness health separate from agent-quality evaluation.

The [packaging decision](../decisions/0012-foundry-hosted-container-packaging.md) records the
boundary and remaining Phase 1D acceptance gates.

## Verified development evidence

On 20 October 2026, the operator-approved registry deployment succeeded through azd. Live checks
confirmed Basic SKU, disabled admin/anonymous access, the Entra project connection, and project
`AcrPull` scoped to that registry. An ACR remote build installed the frozen production lock and
pushed the image successfully. A separate ACR task ran that exact image digest and proved:

- The process runs as UID 10001.
- Hosted startup fails without platform configuration.
- Local development mode becomes ready on loopback.
- Client instruction overrides receive HTTP 400 without a model call.

The smoke task stopped its server on completion. No agent endpoint was created and no platform
identity was simulated as cloud proof. The local source suite has 129 passing tests with clean
Pyright and Ruff checks. Customer resource names, image digest, and run IDs remain in ignored
local evidence, not portable configuration.

### Hosted smoke evidence (21 October 2026)

Version 4 is active after remote image rebuilds and azd deployment. Initial registration rejected
reserved environment-variable overrides; removing them allowed platform injection and startup.
Initial live requests then exposed Foundry's added `agent_reference`. The boundary now validates
the fixed harness name and optional numeric version and strips that inert routing metadata;
it does not permit execution or identity overrides. Regression tests cover this wire shape.

Verified through the real Foundry endpoint:

- Unauthenticated requests with forged platform identity headers receive HTTP 401.
- Client instruction overrides receive HTTP 400.
- An authenticated JSON request completes with a registered service-status tool result.
- An authenticated request carrying forged platform headers and client identity claims still
  completes through the platform boundary; it does not select another tenant's sample data.
- Existing Application Insights contains correlated HTTP, agent, two model calls, and tool
  spans under one trace ID, with counts and no business payload attributes.

Live streaming reaches SSE but ends in `response.failed`. A matching local HTTP reproduction
identified the provider's `rate_limit_exceeded` error for the development model. Stop repeated
model retries once throttling is established; do not increase quota or switch models without
an explicit decision. This is not successful hosted streaming evidence.

Still open: a separately unauthorized Entra principal test, full ingress-bypass acceptance,
successful hosted streaming after capacity is available, live Inspector, evaluation, and complete
azd / Deploy to Azure parity. No evaluation suite was generated during these smoke checks.
Detailed identifiers and trace references stay in ignored local evidence.
