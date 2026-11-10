# ADR 0027: Hosting the Grounded Profile over HTTP

- **Status:** Accepted
- **Date:** 5 November 2026
- **Builds on:** [ADR 0019](0019-local-grounded-execution-path.md), [ADR 0021](0021-caller-authorization-labels.md), [ADR 0023](0023-azure-ai-search-retrieval-adapter.md), [ADR 0024](0024-retrieval-settings-as-profile-configuration.md)

## Purpose and scope

The grounded path only ran from the CLI. This ADR serves it through the same stateless Responses
host as the assistant profile, with streaming, deployment-level caller labels and bounded search
retries. It does not deploy anything: a hosted grounded agent needs its own approval, search
endpoint setting and Search Index Data Reader grant for the agent identity.

## Decision

- **One host, two backends.** The Responses handler talks to a small backend interface. The
  assistant backend wraps `MafAgentExecutor`; the grounded backend wraps `GroundedExecutor`.
  `create_grounded_responses_host` accepts only the `grounded-assistant` profile.
- **Question and history.** The last user message is the question. Earlier user messages are
  history. Questions are limited to 2048 characters; a longer one fails validation before any work.
- **Labels never come from the request.** `GroundedExecutor` resolves labels through a
  `CallerLabelResolver` after `run.started`, so a resolver failure yields `run.failed` before any
  retrieval. `DeploymentCallerLabels` gives every authenticated caller the labels in
  `HARNESS_CALLER_LABELS`, a JSON array. A missing, malformed, duplicated, invalid or over-limit
  value fails startup; `[]` means unlabeled evidence only. The request schema rejects unknown
  fields, so labels cannot be smuggled in. Per-user labels remain a recorded limit (ADR 0021).
  Callers must pass labels explicitly or through a resolver, never both and never neither.
- **Streaming.** `GroundedExecutor.stream` shares its preparation with `run`. Non-answer routes
  stream the fixed text without a model call. The answer route streams through the MAF runtime,
  which now accepts `run_already_started` on `stream` like `run`, and emits `run.failed` if request
  validation fails after the app started the run.
- **Sources.** The host appends a deterministic `Sources:` list built from the citations the model
  was shown. It is not a claim that the answer used every one.
- **Audit.** `retrieval.completed` records only `callerLabelCount`, never label names.
- **Startup.** `agent_http_server` takes `--corpus`, `--corpus-label` and `--fresh-days` for the
  in-memory adapter, which is local only. Hosted serving requires `azure-ai-search`, and the
  Azure adapter reuses the hosting credential.
- **Search retries.** The REST backend retries 429, 503 and connection failures up to three
  attempts within 30 seconds, honoring a `Retry-After` of at most 5 seconds and otherwise backing
  off 0.5 s, then 1 s. A 401 is retried once with a fresh token. Credential failures, other HTTP
  errors and malformed responses are not retried. Errors keep carrying only the status.

## Evidence

Local tests cover JSON, SSE, fixed-text routes, label isolation across endpoints, label injection,
resolver failure, startup failures, and each retry rule. A mutation run over the changed logic
caught 41 of 43 mutants; the two survivors were a missing test for explicit empty labels (added)
and an equivalent check (removed). A loopback host against the live Free search service and a
Foundry model answered JSON and streamed questions with sources, returned fixed text for an
unanswerable one, and rejected a request carrying labels with HTTP 400.

## Limits

- Not deployed. Hosted behavior, the agent identity's search role and the endpoint's label set
  are unproven.
- Label isolation across different label sets was proven with in-memory evidence; the live corpus
  holds unlabeled evidence only.
- The model sometimes prints raw chunk identifiers in its answer because the instruction asks it
  to cite evidence ids. The appended list makes them redundant; changing the instruction needs a
  baseline run.
- Retry behavior was tested with fakes; the transient live failures were not reproduced.
