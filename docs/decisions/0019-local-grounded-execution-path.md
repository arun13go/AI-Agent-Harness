# ADR 0019: Local Grounded Execution Path

- **Status:** Accepted
- **Date:** 4 November 2026
- **Builds on:** [ADR 0017](0017-deterministic-chunk-identity.md), [ADR 0018](0018-bounded-context-plans.md)

## Purpose and scope

Defines how scoped retrieval, the answerability gate, the bounded context plan and the governed
MAF runtime compose into one grounded request, and what the audit trail looks like. It covers the
framework-neutral composition in `app/grounded_executor.py` and the profile settings it relies on.
Azure AI Search, streaming, CLI and HTTP wiring are out of scope.

## Decision

- **Order of operations:** validate the request and labels, retrieve with the authenticated
  identity, re-verify scope, assess answerability, plan context, then call the runtime. Nothing
  reaches the model unless the route is `answer` and at least one evidence item fits the budget.
- **Non-answer routes never call the model.** `clarify`, `abstain` and `escalate` return fixed
  harness text labeled `harness-response`, so malicious, conflicting, weak, stale or missing
  evidence never reaches a model. `retrieve-again` widens the limit (1x, 2x, 3x the base) up to a
  bounded attempt count, with a hard ceiling of five.
- **Usable evidence that cannot fit is insufficient, not answerable.** If planning includes no
  evidence, the run takes the configured insufficient-evidence route with the new reason `unfit`,
  instead of letting the model answer without grounding.
- **The `caveat` route is refused at composition.** It needs a model call with a different
  instruction and has no tests of real-model behavior yet. Failing closed beats half-building it.
- **Defense in depth on scope.** After retrieval the executor checks every item's tenant and label
  set against the caller and raises `AuthorizationError` if a provider returns anything outside
  them. A defective or compromised adapter therefore cannot widen access.
- **Labels come from authentication.** `authorization_labels` is a parameter the host must fill
  from the authenticated principal, never from request content. No host supplies it yet.
- **Citations are what the model was shown.** They list included evidence sources, not a verified
  claim that the answer used them. Omitted usable evidence is returned as handles.
- **One lifecycle per run.** The executor emits `run.started`, then one `retrieval.completed` with
  counts, route, reasons and included evidence identifiers (never text). Non-answer routes end with
  `run.completed` (source `app`). On the answer path the runtime emits `model.*` and the terminal
  event. MAF gained one opt-in argument, `run_already_started`, which suppresses a second
  `run.started` and guarantees the runtime always closes the run, including when request
  validation fails. The default is unchanged, so the hosted path behaves exactly as before.
- **Budget margin.** The context budget is 80% of `maxInputTokens`, the level the MAF runtime
  truncates down to. With the conservative estimator, planned context is never cut a second time.
- **`compaction: summarize` is rejected at load.** No runtime summarizes, so accepting it silently
  truncated. The schema still reserves the value; configuration loading fails with a clear error.
  All shipped profiles now say `truncate-oldest`, which is what they always did.
- **Profiles:** the schema now permits `retrieval.adapter: in-memory` for the grounded profile with
  `strategy: none` (no query-based ranking). `azure-ai-search` still requires `keyword`, `vector` or
  `hybrid`; retrieval and answerability stay mandatory. `grounded-assistant-local.yaml` is the
  in-memory example; `grounded-assistant.yaml` remains the Azure reference.

## Consequences

- The in-memory provider ignores the query and ranks by preset score, so this path proves
  governance, not relevance. A lexical scorer and then the Azure AI Search adapter must pass the
  same conformance suites.
- The minimum evidence score, retrieval limit and attempt count are composition parameters, not
  profile fields. Azure AI Search score scales differ by strategy (hybrid scores are small), so
  thresholds move into validated configuration alongside that adapter.
- The CLI and HTTP host still reject the grounded profile. The hosted image (v7) does not contain
  this code; deploying it is a separate, approved step.
- The ARM template and registration bundle embed the changed core modules and were regenerated.
