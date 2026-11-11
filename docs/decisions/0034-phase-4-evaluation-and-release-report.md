# ADR 0034: Phase 4 Evaluation and Release Report

- **Status:** Accepted (design; nothing built or run yet)
- **Date:** 11 November 2026
- **Builds on:** [ADR 0022](0022-retrieval-relevance-baseline.md), [ADR 0031](0031-lightweight-by-design.md)

## Decision

- **Compose Foundry; build no evaluator.** Quality and safety come from Foundry agent-target batch
  evaluation with built-in evaluators (task adherence, groundedness, tool-call accuracy, indirect
  attack). The service runs them, so the harness needs no evaluation SDK. Per-item results are
  downloaded with `azure-ai-projects`, already installed. Foundry's evaluation tools stay the
  execution path; the harness owns only the three pieces below.
- **Dataset: reuse, do not rewrite.** The 33 questions in `evals/grounded-relevance` become the
  grounded rows (query plus `expected_behavior`: answer, abstain or clarify). A few assistant
  tool-call and prompt-injection rows are added. One small export step produces Foundry's inline
  `inputData`. Provider-side generated suites are not used for gating, because their content
  changes between runs.
- **Thresholds in one file, `evals/release/thresholds.yaml`.** One rule per metric and scorecard:
  policy, quality, safety, latency, cost and telemetry. Floors come from the first measured run and
  are labeled as regression floors, as in ADR 0022. False answers and attack success stay at zero
  by rule. Prices are a customer input in this file; none are hard-coded.
- **Two scorecards, linked by run id.** Quality comes from Foundry results. Operational health
  (error rate, p95 latency, tokens per run, estimated cost) comes from Application Insights through
  a checked-in query, saved as JSON. Neither scorecard can pass the other.
- **One report command, about 150 lines.** `agent-harness release-report` reads the junit XML from
  the deterministic tests, the retrieval-eval JSON, the Foundry results JSON, the telemetry JSON and
  the thresholds, then prints one Markdown report and exits non-zero on any failed rule. It runs
  no model and calls no service, so it is testable offline. Regression compares against a
  committed baseline file with a tolerance.
- **Pull requests stay deterministic.** Tests and `eval-retrieval` run on every change. Foundry
  evaluations run before a release or on a schedule (Phase 5 wires the schedule).

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
  inputs; the Application Insights query is untested.
