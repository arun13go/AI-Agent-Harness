# ADR 0030: Hosted Grounded Agent as a Second Foundry Agent

- **Status:** Accepted
- **Date:** 10 November 2026
- **Builds on:** [ADR 0010](0010-stateless-governed-responses-host.md), [ADR 0011](0011-single-tenant-foundry-ingress-identity.md), [ADR 0027](0027-grounded-http-hosting.md)

## Purpose and scope

ADR 0027 served the grounded profile locally. This ADR records how it runs as a hosted Foundry
agent beside the assistant agent, what was proven live, and what it costs to repeat. The test agent
was deleted after verification; the assistant agent (`agent-harness`, version 7) was untouched.

## Decision

- **A second hosted agent, not a new version.** A new version of `agent-harness` would replace the
  assistant at 100% traffic. The grounded profile deploys as `agent-harness-grounded` from the same
  image.
- **Two deployment settings select the behavior.** `HARNESS_AGENT_NAME` sets the name the host
  answers to (default `agent-harness`). A request or agent reference naming any other agent is
  refused. `HARNESS_PROFILE` names a profile file in `config/profiles` (a bare `.yaml` name only);
  `--profile` still overrides it. Both are validated at startup.
- **Settings for the grounded agent:** `AZURE_SEARCH_ENDPOINT`, `HARNESS_CALLER_LABELS` (`[]` until
  an operator sets labels), `AZURE_AI_MODEL_DEPLOYMENT_NAME`. The image allowlist
  (`.dockerignore`) now includes `grounded-assistant.yaml`; without it the container has no
  profile to load.
- **Identity and scope.** The hosted tenant scope is the platform's agent tenant ID, which equals
  the Entra tenant ID in this deployment, so the corpus must be synced under that value with
  `search-index --action sync --tenant`. The agent's instance identity needs
  `Search Index Data Reader` on the search service. Index data under any other tenant value was
  not returned.
- **No committed second manifest.** `azd` refuses a service path outside the manifest folder
  (`../..` is rejected), so a second manifest cannot sit under `deploy/` and build from the repo
  root. The test deployment used a staging folder holding a copy of the repo files, its own
  `azure.yaml` (service `grounded`, `project: .`, remote build) and its own azd environment. Making
  this repeatable without a copy needs a decision of its own.

## Evidence

On a hosted agent with a Free search service: a supported question returned a cited answer with a
`Sources:` list; an unanswerable question returned the fixed text with zero model output; a
streamed question returned ordered events ending in `response.completed`; and a request carrying
`authorization_labels` was rejected with HTTP 400. Version 7 still answered afterwards. Deploy took
about 2 minutes 15 seconds.

## Limits

- Per-user labels remain unavailable (ADR 0021); all callers share the endpoint's label set.
- Cross-label isolation was proven in-process only; the live corpus is unlabeled.
- The test agent and its role assignment were removed. The synced corpus remains in the index
  under the tenant value.
- Hosted compute is billed per active vCPU-hour and GiB-hour (about $0.12 per hour for 1 vCPU and
  2 GiB, from a web search not checked against official pricing); idle compute reportedly scales to
  zero.
- Callers' Foundry endpoint RBAC and the agent's registration through the ADR 0015 bridge were not
  exercised for the second agent.
