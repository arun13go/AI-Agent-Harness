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

## Register the hosted agent (both entry points)

Per [ADR 0016](../decisions/0016-registration-outside-deployment-scripts.md), neither `azd up`'s
provisioning nor Deploy to Azure registers an agent. Both create the same infrastructure and
outputs; one shared step then builds the image in ACR and registers a hosted-agent version with
your own Entra identity. It needs no shared-key storage access and no new role grants for the harness.

1. **Provision.** Use either `azd provision` or the **Deploy to Azure** button.
2. **Record outputs (portal path only).** Copy these deployment outputs into the azd environment
   (`azd env set <name> <value>`): `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_REGISTRY_NAME`,
   `AZURE_CONTAINER_REGISTRY_ENDPOINT`, `AZURE_AI_PROJECT_ID`, `AZURE_AI_PROJECT_ENDPOINT`,
   `FOUNDRY_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME`. `azd provision` records them itself.
3. **Build and register.** Run `azd deploy` from the repository root. It builds remotely in ACR and
   registers a hosted-agent version. `azd deploy` has no preview for hosted agents. In the dev
   project the endpoint's version selector is `@latest` (100%), so a new version would serve traffic
   immediately. Pin the serving version first (`azd ai agent endpoint update`) when you need a candidate.
4. **Smoke test the candidate.** While production is pinned, create a session bound to the new
   version (`azd ai agent sessions create agent-harness <version>`) and POST
   `{"input": "...", "stream": false, "agent_session_id": "<id>"}` to the agent endpoint; repeat
   with `"stream": true`. The host accepts and strips `agent_session_id` as routing metadata
   and still rejects `conversation`, stored state and instruction overrides. `azd ai agent invoke`
   sends a platform `conversation`, so it cannot exercise this stateless host. Check JSON, SSE,
   tool call and traces before treating the environment as parity-proven. A successful ARM
   deployment is not that proof.

The required outputs are asserted by `tests/deployment`, and CI rejects drift between the Bicep and
the generated ARM. The ADR 0015 bridge is not part of this path. Cloud resources, role grants and
endpoint changes still need separate approval.

## Verified state

Hosted agent `agent-harness` v6 is active and serves production; the endpoint selector is pinned to
it at 100% (Responses 2.0.0). Versions 4 (previous release, kept for rollback) and 5 (superseded
candidate) remain. Verified through the Foundry endpoint:

- Unauthenticated requests return 401; client instruction overrides return 400.
- Authenticated JSON and SSE requests complete with a registered tool result. Streaming can return
  a transient `model_provider_error` when sent immediately after another request and succeeds after a
  short pause; the cause is unconfirmed (suspected model rate limit).
- Application Insights shows correlated HTTP, agent, model and tool spans (operation names and token counts only, no payloads).
- The container runs as UID 10001 and fails closed without platform configuration.
- Local Agent Inspector service-health request was operator-confirmed; this is not hosted or trace-panel proof.

Open: unauthorized-principal (403) test, full ingress acceptance, evaluation, and `azd`/Deploy to Azure parity.
The account uses `publicNetworkAccess=Enabled` and `disableLocalAuth=true`; it is not network-isolated.
Customer identifiers and run IDs stay in ignored local evidence.

## Registration bridge (optional, disabled by default)

**Purpose:** let the Deploy to Azure path register the image as a hosted-agent version, as `azd` does.
**Scope:** deployment tooling only; not request handling. Design: [ADR 0015](../decisions/0015-passwordless-hosted-deployment-bridge.md).
**Status:** implemented and tested locally; never run successfully in the cloud.

| Piece | Location |
|---|---|
| Release/target contract | [deployment.py](../../src/agent_harness/adapters/foundry/deployment.py) |
| Registration runner | [registration.py](../../src/agent_harness/adapters/foundry/registration.py), [register_hosted_agent.py](../../src/agent_harness/app/register_hosted_agent.py) |
| Bundle packaging | [package_registration.py](../../src/agent_harness/app/package_registration.py), [registration-bundle.json](../../infra/registration-bundle.json) |
| ARM deployment script | [registration.bicep](../../infra/registration.bicep), [register-agent.sh](../../infra/scripts/register-agent.sh) |

Runner behavior:

- Managed identity only; requires `--managed-identity-client-id` and `--exclusive-deployment-owner`.
- At most one create request; ambiguous outcomes return `deployment_outcome_unknown` (do not rerun blindly).
- Never patches or promotes endpoints. An existing agent needs a concrete serving pin; a first deployment needs `--allow-initial-exposure`.
- Emits a small receipt without prompts, tokens or provider bodies.

Enable by passing `registrationConfig` to [main.bicep](../../infra/main.bicep) (not during `azd up`):

```json
{ "registrationConfig": { "value": {
  "identityName": "<existing-identity>",
  "image": "<registry>.azurecr.io/agent-harness@sha256:<digest>",
  "exclusiveDeploymentOwner": true,
  "allowInitialExposure": false } } }
```

The identity and image must already exist; the bridge creates neither and grants no roles.
Needed identity permission (proposed, not created): project-scoped `Microsoft.CognitiveServices/accounts/AIServices/agents/read` and `agents/write`.

Regenerate assets after changing runner sources or dependencies:

```powershell
uv export --frozen --only-group deployment --no-header --no-annotate --output-file infra\registration-requirements.txt
uv run --frozen python -m agent_harness.app.package_registration
az bicep build --file infra\main.bicep --outfile infra\main.json
uv run --frozen pytest tests/deployment -q
```

Execution limits: AVM `deployment-script` 0.5.2, Azure CLI 2.89.1, venv 60 s, install 300 s, runner 660 s,
script 20 min, cleanup `Always`, 24 h retention.

### Current limitations

- The deployment-script storage fails in this tenant (`KeyBasedAuthenticationNotPermitted`). Do not enable shared keys; a compatible execution design or governance decision is required.
- Script-service Python/dependency execution and live runner compatibility are unverified.
- Image delivery and portal template publication are open.
