# ADR 0021: Caller Authorization Labels Come From the Authenticated Endpoint

- **Status:** Accepted
- **Date:** 4 November 2026
- **Builds on:** [ADR 0011](0011-single-tenant-foundry-ingress-identity.md), [ADR 0013](0013-foundry-rbac-ingress-authorization.md), [ADR 0019](0019-local-grounded-execution-path.md)

## Purpose and scope

The grounded executor filters evidence by the caller's authorization labels and requires that they
come from authentication, never from request content. This ADR decides what "from authentication"
means for the hosted path, given what the platform actually provides. It does not design a
directory, entitlement store or evidence-labeling workflow.

## Context

- Foundry authenticates callers at its ingress and enforces endpoint RBAC (ADR 0013). The container
  receives an opaque user key and call ID, not claims or group membership.
- The container does not receive the end user's token, and delegated end-user identity is not
  configured (ADR 0013). No pre-invocation lookup of the user key is documented, which is why
  per-user enrollment was dropped. User keys are not assumed to be Entra object IDs, so mapping a
  key to directory groups is unproven.
- Evidence labels already exist on every chunk and are enforced twice: by the retrieval provider
  and again by the executor, which fails closed if a provider returns anything outside the caller's
  set.

## Options considered

| Option | Verdict |
|---|---|
| A. Labels in the request body | Rejected. Any caller could widen their own access, and retrieved or tool text could try to. |
| B. A fixed label set per deployed agent endpoint, from deployment configuration | **Chosen.** Endpoint RBAC decides who may call; the label set decides what that audience may read. |
| C. Group claims from a verified end-user token | Not available: the container never receives that token. Revisit if Foundry documents a verified claims path. |
| D. Look up directory groups with the agent's identity | Blocked: the opaque key is not a documented directory identifier. |
| E. A per-user entitlement store keyed by the user key | Blocked by the same enrollment problem that removed the allowlist. |

## Decision

- **One label set per endpoint.** The hosted grounded profile reads its caller labels from
  deployment configuration (`HARNESS_CALLER_LABELS`, a JSON array), validated at startup with the
  existing label validator and bounded to 64 labels. They are identical for every caller of that
  endpoint. Audiences with different access are served by different agent endpoints, each with its
  own role assignments and label set, over the same or separate indexes.
- **Explicit, fail-closed configuration.** A missing, malformed, oversized or duplicate-bearing
  value stops startup. `[]` is a valid, deliberate choice meaning "unlabeled evidence only"; a
  missing variable is never treated as empty.
- **Never from the profile, the request, or model output.** Profile YAML selects implementations
  and thresholds and must not widen access. The HTTP schema already rejects unknown request fields,
  and labels are never read from tool, retrieval or model text.
- **A resolver port keeps the decision replaceable.** The host obtains labels through a
  `CallerLabelResolver` (`resolve(identity) -> labels`). The only implementation now returns the
  configured set for every identity. A future implementation may map an identity to labels once a
  verified source exists (options C to E) without changing the executor.
- **Resolver failure fails closed.** A resolver error, or output that does not validate, ends the
  request before retrieval with a typed error and a `run.failed` event.
- **Unlabeled evidence is readable by every caller of the deployment.** Ingestion must label
  anything sensitive. This is the main way the design can be misused, so ingestion guidance must
  say it plainly.
- **Audit records counts, not labels.** `retrieval.completed` gains the number of caller labels;
  label values are not logged.

## Consequences

- **No per-user access control within an endpoint.** Two users who may call the same endpoint see
  the same evidence. Fine-grained access needs one of options C to E, which depend on platform
  capabilities we cannot verify today. Revisit when Foundry documents a verified claims path or a
  user-key lookup.
- **Cost scales with audiences.** Each distinct audience needs its own agent endpoint and RBAC
  assignment. That is acceptable for a template's reference scenarios and a real limit for large
  deployments.
- **Sequencing changes.** An in-memory corpus baked into a container image is a demonstration, not
  a hosted design. Hosting the grounded profile over HTTP (with streaming) should follow the Azure
  AI Search adapter, which supplies a real corpus and label-filtered queries. The relevance baseline
  and the adapter come first.
- **Acceptance when hosted:** startup tests for every bad configuration; an HTTP test that a
  request cannot carry labels; and a deployment check that two endpoints with different label sets
  over one corpus return different evidence.
- **The executor docstring** ("labels must come from authentication") should be refined to "from
  the authenticated deployment context" when the host is wired.
