# ADR 0016: Register the Hosted Agent Outside ARM Deployment Scripts

- **Status:** Accepted
- **Date:** 28 October 2026
- **Amends:** [ADR 0015](0015-passwordless-hosted-deployment-bridge.md) execution path; [ADR 0003](0003-dual-azure-deployment-entry-points.md) parity wording

## Purpose and scope

Chooses how a hosted-agent version is registered when tenant policy forbids storage shared-key
authentication. It covers deployment tooling only; request handling and caller authorization are
unchanged ([ADR 0013](0013-foundry-rbac-ingress-authorization.md)).

## Context

- The hosted agent is a Foundry data-plane resource with no ARM type, so ARM alone cannot create it.
- The ADR 0015 bridge ran as an `Microsoft.Resources/deploymentScripts` resource. The service
  creates supporting storage and mounts a file share (`/mnt/azscripts`) for script input and output.
  Microsoft documents no keyless mode.
- The 22 Oct 2026 probe failed with `KeyBasedAuthenticationNotPermitted` before any user script ran. No
  policy, role or endpoint changed. Tenant policy must not be weakened.
- Product requirement: Deploy to Azure and `azd up` need equivalent parameters, RBAC, outputs and
  smoke tests ([ADR 0003](0003-dual-azure-deployment-entry-points.md)).

## Options

| Option | Keyless | Deploy succeeds only if registered | New billable resources | Verdict |
|---|---|---|---|---|
| A. Provision with ARM or `azd`; register in a separate, explicit step | Yes | No (separate step) | None | **Chosen now** |
| B. Same runner from CI using workload-identity federation | Yes | No (CI gate instead) | None | Phase 5 |
| C. Container Instances or Container Apps job running the runner | Yes | No (ARM completes at container start) | Yes | Deferred |
| D. Enable shared keys or exempt the policy | No | Yes | Yes | Rejected |

Option C removes the deployment-script storage but ARM cannot wait for the container to finish, so
a green deployment would not prove registration. It also needs a runner image, which does not exist.

## Decision

1. **Deploy to Azure and `azd up` provision the same infrastructure** and expose the same outputs.
   Neither path registers an agent during provisioning.
2. **One shared registration step** follows either path. It uses `azd deploy` for the agent
   service, which already registers hosted-agent versions with the operator's own Entra identity.
3. **Deploy to Azure outputs** the values the shared step needs, so the portal path needs no
   undocumented knowledge.
4. **Keep the ADR 0015 bridge** disabled and experimental. It is not part of the supported path
   and not a v1.0 dependency.
5. **Phase 5** adds Option B: the runner with a workload-identity credential. The runner today is
   managed-identity-only, so that needs a typed credential choice and tests.
6. **Parity is redefined** as equivalent infrastructure, RBAC and outputs, followed by the same
   registration and smoke test. Hosted-agent versions are data-plane objects, so ARM never owns them.

## Consequences

- No tenant policy change, no shared keys, no new billable resources.
- Portal users take one extra documented step; Deploy to Azure alone does not create a running agent.
- The deployment smoke test must run after registration and gates parity, not provisioning.
- `registrationConfig` and its tests stay until the bridge is retired; retiring it needs a new ADR.
- Cloud resources, role grants, publication and endpoint changes each still need separate approval.

## Open gates

- Operator acceptance of this ADR (done, 28 October 2026).
- A documented post-provision registration command and outputs, with CI checks that the ARM and
  `azd` outputs match.
- Image delivery and portal template publication.
- Least-privilege registration role, if Option B is later adopted (Phase 5).
- Unauthorized-principal (403) and full ingress acceptance.
