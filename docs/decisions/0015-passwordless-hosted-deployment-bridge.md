# ADR 0015: Registration Bridge for the Foundry Hosted-Agent Data Plane

- **Status:** Accepted design; implemented locally, disabled by default; not cloud-validated
- **Date:** 22 October 2026

## Purpose and scope

Defines an optional deployment helper that lets the **Deploy to Azure** path register the harness
image as a Foundry hosted-agent version, as `azd` already does. It is deployment tooling only: it
does not handle requests, replace Foundry hosting, or change caller authorization
([ADR 0013](0013-foundry-rbac-ingress-authorization.md)).

## Where it sits in the repo

| Layer | Artifact |
|---|---|
| Contract | [deployment.py](../../src/agent_harness/adapters/foundry/deployment.py) |
| Runner | [registration.py](../../src/agent_harness/adapters/foundry/registration.py), [register_hosted_agent.py](../../src/agent_harness/app/register_hosted_agent.py) |
| Packaging | [package_registration.py](../../src/agent_harness/app/package_registration.py), [registration-bundle.json](../../infra/registration-bundle.json), [registration-requirements.txt](../../infra/registration-requirements.txt) |
| Infra | [registration.bicep](../../infra/registration.bicep), [register-agent.sh](../../infra/scripts/register-agent.sh), optional `registrationConfig` in [main.bicep](../../infra/main.bicep) |
| Tests | [tests/deployment](../../tests/deployment) |

Usage and regeneration commands: [Azure Dev guide](../getting-started/azure-dev.md#registration-bridge-optional-disabled-by-default).

## Context

ARM/Bicep creates the registry, pull role and project connection. The hosted agent itself is a
Foundry data-plane resource (no native ARM type), so the generated ARM template alone cannot
register it. The bridge closes that gap with an ARM deployment script.

## Decision

- Add an opt-in `Microsoft.Resources/deploymentScripts` bridge; the script is embedded, never fetched from a URL.
- Authenticate with a dedicated, pre-existing user-assigned managed identity (`ManagedIdentityCredential` only).
- Register an existing, digest-pinned image (`registry/repo@sha256:<digest>`); the bridge does not build images.
- Keep one release contract (agent name, Responses 2.0.0, CPU 1 / 2Gi, allowed settings) that renders both the `azd` and REST definitions.
- Require an exact public-Azure project endpoint that matches the resource ID.
- Disabled by default; never runs during ordinary `azd up` (one writer at a time).

## Runner controls

- At most one create request per run; uncertain outcomes reconcile by attempt ID, else fail `deployment_outcome_unknown`.
- Never patches or promotes endpoints. An existing agent needs a concrete serving pin; a first deployment needs explicit initial-exposure acknowledgment and an exclusive-owner acknowledgment.
- Bounded reads, pagination, redirects, response sizes and polling.
- Receipt and errors carry no prompts, tokens or provider bodies.

## Packaging and execution

- Bundle: allowlisted runner sources plus hash-locked requirements; SHA-256 checked at start.
- Bootstrap: Python 3.12, temporary installer venv (60 s), `pip --require-hashes --only-binary=:all:` from public PyPI (300 s), runner (660 s), script (PT20M).
- Bicep: AVM `deployment-script` 0.5.2, Azure CLI 2.89.1, existing identity only, `cleanupPreference=Always`, 24 h retention, telemetry off.
- Temporary Container Instances and Storage are billable during execution.

## Permissions

| Principal | Needs | Not allowed |
|---|---|---|
| Registration identity | Project-scoped data actions `Microsoft.CognitiveServices/accounts/AIServices/agents/read` and `agents/write` (custom role proposed, not created) | Broader Foundry roles; the bridge assigns no roles |
| Hosted agent runtime | Existing identity, unchanged | Sharing the registration identity |

`agents/write` can also change endpoints, so "no promotion" is a runner control, not RBAC.

## Current limitations and open gates

- Deployment-script supporting storage fails in this tenant (`KeyBasedAuthenticationNotPermitted`). Shared keys and tenant policy must not be weakened. [ADR 0016](0016-registration-outside-deployment-scripts.md) (accepted) registers outside deployment scripts; this bridge stays experimental.
- Script-service Python/dependency execution and live runner compatibility are unverified.
- Custom role not created or granted.
- Image delivery and portal template publication are open.
- Both deployment paths need equivalent acceptance (JSON, SSE, tools, traces) and CI drift checks on the generated ARM.
- Cloud resources, role grants, publication and endpoint changes each require separate approval.
