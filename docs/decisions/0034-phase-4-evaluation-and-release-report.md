# ADR 0034: Phase 4 Evaluation and Release Report

- **Status:** Accepted (design). Revised 13 November 2026 after run 1: MAF primary, Foundry service
  path deferred
- **Date:** 11 November 2026
- **Builds on:** [ADR 0022](0022-retrieval-relevance-baseline.md), [ADR 0031](0031-lightweight-by-design.md)

## Decision

- **Source rule: Foundry or MAF only.** Every evaluator comes from the Foundry SDK or the MAF SDK;
  the harness builds none. Where neither offers one, the gap is listed below with the reason, and
  an open-source package is added only where it fits and an ADR approves it. Each result records its
  source in the run label `eval_source` (`maf-local`, `maf-foundry` or `foundry-service`).
  `FoundryEvals` is a thin wrapper over the Foundry evals API (`client.evals`), so those two share
  one judge; they differ in who runs the agent.

  | Need | Source | Gap or note |
  |---|---|---|
  | Tool call, tenant scope, injection (every pull request) | MAF `evaluate_agent` + `LocalEvaluator` | None. Free and deterministic |
  | Judged quality and safety (before a release) | MAF `FoundryEvals`, `num_repetitions` to measure noise | MAF lists no indirect-attack evaluator; direct injection is covered locally. No open-source package yet |
  | Deployed-endpoint check | Foundry service (`client.evals`) | Blocked by the strict host (run 1). Deferred |
  | Retrieval relevance | Harness `eval-retrieval` (ADR 0022) | **Gap:** no SDK evaluator verified for offline search-index recall. Kept; no open source added |
  | Release verdict | Harness `release-report` | Aggregates results; not an evaluator |

  MAF evaluation is experimental, so it sits behind a feature flag with a fallback (AGENTS.md).
  A hook that runs `evaluate_agent` through the governed path (approvals, audit) is untested; a
  scripted-model spike comes first.
- **Dataset: reuse, do not rewrite.** The 33 questions in `evals/grounded-relevance` become the
  grounded rows (query plus `expected_behavior`: answer with named sources, or decline without
  guessing). Six assistant rows (`evals/release/assistant.yaml`: tool call, unavailable service,
  other-tenant scope, two injections) are added. `agent-harness export-eval-rows` writes either set
    as the JSON array the Foundry service path takes as `inputData` (secondary path only); each row holds exactly `query` and
  `expected_behavior`, and row order follows the source file. Provider-side generated suites are
  not used for gating, because their content changes between runs.
- **Thresholds in one file, `evals/release/thresholds.yaml`.** One rule per metric and scorecard:
  policy, quality, safety, latency, cost and telemetry. Floors come from the first measured run and
  are labeled as regression floors, as in ADR 0022. False answers and attack success stay at zero
  by rule. Prices are a customer input in this file; none are hard-coded.
- **Two scorecards, linked by run id.** Quality comes from MAF or Foundry results. Operational health
  (error rate, p95 latency, tokens per run, estimated cost) comes from Application Insights through
  a checked-in query, saved as JSON. Neither scorecard can pass the other.
- **One report command.** `agent-harness release-report` (`app/release_report.py`, about 350
  lines with its validation) reads the junit XML from the deterministic tests, the retrieval-eval
  JSON, any number of normalized `{scorecard: {metric: number}}` files (Foundry and telemetry
  results arrive this way) and the thresholds. It returns JSON, writes Markdown with `--markdown`
  and the merged metrics with `--json-out`, and exits non-zero on any failed rule. It runs no model
  and calls no service, so it is testable offline. The thresholds must cover every required
  scorecard, a rule whose metric is missing fails, two sources reporting one metric is an error,
  and inputs are size-capped (junit also refuses a DOCTYPE or entity). Regression compares against
  the saved metrics of an earlier run with a relative tolerance (default 5%). Run labels (model,
  dataset, prompt versions) are listed in the report for traceability; they are restricted to plain
  characters so they cannot alter the Markdown.
- **Pull requests stay deterministic.** Tests, `eval-retrieval` and `LocalEvaluator` checks run on
  every change. Judged evaluations run before a release or on a schedule (Phase 5 wires the
  schedule).

## Not built

Custom evaluators, a dashboard, a report UI, trace-to-dataset harvesting (EVAL-08), continuous
evaluation, prompt optimization, and `summarize` compaction (ADR 0029). Foundry offers the first
three of these, and they can be added on top of the same results file.

## Limits and to verify at build

- An evaluation run bills judge-model tokens on the project's `gpt-5-mini` deployment, needs a
  deployed agent, and creates evaluation objects in the project. Each needs separate approval.
  Whether `gpt-5-mini` is a suitable judge is unmeasured.
- The grounded agent was deleted after ADR 0030, and redeploying it needs the staging copy. The
  assistant agent (v7) is deployed.
- Judge scores are noisy. The first live run sets floors, not targets, and one run proves nothing
  about stability. Repeat runs are needed before thresholds are trusted.
- Foundry result fields and the evaluator names must be confirmed against the live service before
  the report parser is written, so the parser is built from a saved real result, not from docs.
- Telemetry exists for traces and token counts (`gen_ai.usage.*`); cost needs the customer's price
  inputs. **Correction:** this ADR first said the Application Insights resource held no telemetry.
  That was wrong. The CLI's default one-hour window had cut off my longer filter, so older data read
  as empty. The resource receives the hosted agent's telemetry. Run 1 confirmed `harness.http`
  requests (with `success` and a millisecond `duration`); the platform also records its own
  `invoke_agent` requests in the same table, which the query excludes by name. The
  `harness.chat` dependencies and token keys are still unchecked, because no model call happened.
- Assistant rows assume the hosted agent's one tool and one service (`api`); the injection rows
  test direct injection only, because the tool's output is fixed and cannot carry an indirect one.
- **Run 1 result: no scores.** All 6 rows errored (`TARGET_INVOCATION_FAILED`, HTTP 400). The
  container log showed why: the evaluation service sends top-level request fields `conversation`,
  `include` and at least one more (not named in the log by design), and the host's strict request
  schema (ADR 0010) rejects any unknown field. So the boundary worked as designed, but the agent
  cannot be evaluated by Foundry until the host accepts what platform clients send. Even then, the
  HTTP response carries only message text, so tool-call evaluators may have no tool calls to score.
  Run 1 therefore set no floors and created no thresholds file.
