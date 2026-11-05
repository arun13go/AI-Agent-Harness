# ADR 0024: Retrieval Settings as Validated Profile Configuration

- **Status:** Accepted
- **Date:** 5 November 2026
- **Builds on:** [ADR 0019](0019-local-grounded-execution-path.md), [ADR 0023](0023-azure-ai-search-retrieval-adapter.md)

## Purpose and scope

The grounded path took its score threshold, retrieval limit and attempt count as function
arguments, and refused the Azure AI Search adapter outright. This ADR moves those settings into
the profile, validated by the schema and the typed model, and lets a profile select the Azure
adapter. It adds no hosting and no new retrieval strategy.

## Decision

- **New `retrieval` settings** (all optional except where noted): `limit` (1 to 100, default 8),
  `minimumScore` (0 to 1, default 0.5), `maximumAttempts` (0 to 4, default 1), and a `search`
  block that is required with `adapter: azure-ai-search` and forbidden otherwise. The defaults
  equal the previous hard-coded values, so existing profiles behave as before.
- **`search` block.** `endpointEnv` names the environment variable holding the service address, so
  no deployment address lives in a profile (the same pattern the model settings use), and
  `indexName` names the index. `keywordScoreHalfPoint` tunes keyword scores and is rejected with
  the semantic strategy, so no setting is accepted and silently ignored.
- **Strategies.** `semantic` is new: keyword retrieval re-ranked by the service's semantic ranker,
  scored on its 0 to 4 scale. `keyword` and `semantic` are implemented. `vector` and `hybrid` stay
  in the schema as reserved values and are refused with a clear error at composition, so a
  profile can never run a weaker mode than it names.
- **Scope of validation.** The schema and the typed model enforce the same rules (ranges, the
  presence or absence of `search`, semantic only with Azure), so hand-built configurations are
  checked too. Mandatory controls remain outside application configuration.
- **Composition.** `create_grounded_executor` takes the threshold, limit and attempts from the
  profile unless a caller passes an explicit value, as an evaluation sweeping the threshold does.
  `create_azure_search_provider` turns a profile and the environment into a provider: the
  strategy selects the backend mode and the score mapping (linear for the reranker, saturating
  for keyword), and a missing or blank endpoint variable is a configuration error that names the
  variable, never its value. The endpoint is validated before any token is requested
  ([ADR 0023](0023-azure-ai-search-retrieval-adapter.md)).
- **CLI.** `grounded-run` and `eval-retrieval` build the provider from the profile. With Azure
  retrieval, `--corpus` is refused because the index is external; with the in-memory adapter it
  is required. `--minimum-score` overrides the profile. The ad-hoc `--search-endpoint`,
  `--search-half-point` and `--search-semantic` flags are removed in favor of the profile, and
  `--search-index` remains as an override. Every refusal happens before a model client exists.
- **Reference profile.** `grounded-assistant.yaml` now selects `semantic` with a 0.625 minimum
  score, 8 results and one retry.

## Live validation (5 November 2026)

- `eval-retrieval --profile config/profiles/grounded-assistant.yaml`, with no overrides, scored
  the Azure index: answer rate 0.59, source recall 0.55, citation precision 1.00, no false
  answers, and every committed guard passed. This matches ADR 0023's manual run.
- A real `grounded-run` answered "How do I restart the API service?" from the Azure index with a
  `gpt-5-mini` call and a citation to `restart-procedures.md`, and answered an off-topic question
  with a weak-evidence clarification and no model call. This is the first end-to-end run through
  Azure retrieval and a Foundry model. It judges routing and citation presence only; whether
  answers are faithful to the evidence is Phase 4 evaluation.
- One `eval-retrieval` run exited with a typed error right after the index was first created, and
  five later runs, including one on a freshly recreated index, did not. The cause is unknown. The
  REST backend does not retry throttled or unavailable responses, which is the first thing to add
  before hosting.

## Consequences

- A profile can select the Azure adapter, and the profile setting is no longer refused. Hosting
  over HTTP, which needs the caller-label wiring from [ADR 0021](0021-caller-authorization-labels.md),
  is the next step.
- The 0.625 cutoff is provisional: it was chosen on the 33-question evaluation set it is scored
  on, over a 24-chunk synthetic corpus. A larger and more realistic set should confirm it.
- `limit`, `minimumScore` and `maximumAttempts` apply to every retrieval adapter, so a customer can
  tune the in-memory path the same way.
