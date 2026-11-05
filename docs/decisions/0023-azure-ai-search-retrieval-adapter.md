# ADR 0023: Azure AI Search Retrieval Adapter Behind a Backend Port

- **Status:** Accepted (amended 5 November 2026 after the first live run)
- **Date:** 4 November 2026
- **Builds on:** [ADR 0019](0019-local-grounded-execution-path.md), [ADR 0021](0021-caller-authorization-labels.md), [ADR 0022](0022-retrieval-relevance-baseline.md)

## Purpose and scope

The reference retrieval adapter for Azure AI Search: the provider, the query filter, the index
document shape, a REST backend, and the live checks that exercised them on a Free-tier service.
It does not wire the adapter into configuration, and it does not claim that retrieval quality
improves. The live results below show that keyword search alone does not.

## Decision

- **Backend port.** `AzureSearchRetrievalProvider` depends on a one-method `SearchBackend`
  (`search(text, odata_filter, top)`). Tests use a fake; the live backend is
  `AzureSearchRestBackend`.
- **REST over an SDK, for now.** The backend calls the stable Search REST API (`2024-07-01`) with
  an Entra token from the already-declared `azure-identity` dependency, using only the standard
  library. The `azure-search-documents` SDK could not be downloaded from this machine (the package
  file host failed the TLS handshake), and the lock file cannot be updated without it. A new
  dependency is not needed for this slice. Moving to the SDK later changes only the backend. The
  backend validates the endpoint (https, an Azure Search host, no credentials, port, path or
  query) before requesting any token, refuses redirects, caches the token until five minutes
  before expiry, bounds response size, and reports an HTTP status only, never a response body.
- **Scope is enforced in two places because the service cannot express the rule.** The rule is
  that every label on a document must be one the caller holds. Inside `all`, the service accepts
  only negated tests, so the rule is not a single filter (found live; the first design used
  `all(... search.in ...)` and every labeled query failed with HTTP 400). The service therefore
  applies a safe superset: the tenant matches, and the document is unlabeled or shares at least
  one label with the caller. The provider enforces the exact rule on the results and drops
  documents that need a label the caller lacks.
- **Fail closed on backend defects.** A result from another tenant, or a labeled result sharing no
  label with the caller, can only come from a wrong filter or index, so it raises
  `AuthorizationError` without echoing the document. Partial overlaps are expected under the
  superset filter and are dropped, not errors.
- **Over-fetch.** Because documents are dropped after ranking, the provider asks for five times
  the limit (minimum 50, maximum 1,000). If more than that many higher-ranked documents need
  labels the caller lacks, visible evidence can be missed. That recall limit is accepted for now
  and is the reason the live check must be rerun when label use grows.
- **No string splicing of untrusted values.** Tenant and labels pass the existing validators,
  whose patterns exclude quotes, commas, spaces and control characters, and values are quoted as
  OData literals as a second layer. Only `tenantScope` and `authorizationLabels` are filterable.
- **Scores are mapped, not trusted.** Service scores are unbounded and not comparable across
  queries, but answerability needs [0, 1]. The provider requires an explicit monotonic mapping;
  `saturating_score(half_point)` is the first. Zero scores are dropped and ties break by
  identifier, so ordering is deterministic.
- **Untrusted output.** Every field is type- and size-checked. Malformed, duplicate or excess
  results raise `RetrievalError` with a generic message. The provider stamps `retrieved_at` and
  the `untrusted-retrieved-content` label itself and never stores either in the index.
- **Index shape.** `INDEX_FIELDS` declares the chunk identifier as the key, searchable content,
  lineage, freshness, tenant, labels and evidence flags. `evidence_to_document` is the only writer,
  and `index_definition` states every field attribute explicitly.
- **Infrastructure.** `infra/search.bicep` creates a keyless service (`disableLocalAuth`, one
  replica and partition, semantic ranker `disabled` unless `searchSemanticSearch` is `free`).
  `main.bicep` creates it only when
  `searchServiceSku` is `free` or `basic`, optionally in its own resource group
  (`searchResourceGroupName`). The default creates nothing, so `azd up` and the Deploy to Azure
  button are unchanged. Roles are never granted by the template.

## Live validation (Free tier, 5 November 2026)

- **Isolation.** The first run failed as described above. After the fix, a canary index with two
  tenants and labeled documents passed 8 of 8 cases: the no-label, one-label, two-label and
  unrelated-label callers of one tenant, both callers of another tenant, and a third tenant that
  sees nothing. `agent-harness search-index --action isolation` repeats it.
- **Relevance.** The 33-question baseline was scored on the Azure index with raw keyword (BM25)
  scores mapped by `score / (score + 1)`:

| Minimum score (raw cutoff) | Answer rate | Source recall | Citation precision | False answers |
|---|---|---|---|---|
| 0.5 (1.0) | 1.00 | 0.95 | 0.42 | 0.82 |
| 0.7 (2.3) | 1.00 | 0.82 | 0.53 | 0.64 |
| 0.8 (4.0) | 0.68 | 0.68 | 0.81 | 0.55 |
| 0.9 (9.0) | 0.27 | 0.18 | 1.00 | 0.09 |
| Lexical baseline (0.5) | 0.50 | 0.45 | 1.00 | 0.00 |

  Keyword search finds the right documents, including paraphrases the lexical scorer missed, but
  its score cannot separate answerable questions from near-misses at any cutoff: the settings that
  keep recall answer most unanswerable questions, and the settings that abstain lose recall. The
  baseline's zero false-answer guard therefore cannot be met with this scorer, and the adapter
  must not be enabled for answering with it. The set has 33 questions on a 24-chunk corpus, so the
  shape is the finding, not the exact numbers.

## Semantic ranker (Free plan, 5 November 2026)

The free semantic plan was enabled on the same service (about 1,000 queries a month, no cost; the
what-if showed only `semanticSearch` changing) and the index gained a semantic configuration over
the content field. With `--search-semantic` the backend reports the reranker score (0 to 4), and
results the service did not re-rank count as zero. `linear_score(4.0)` maps it to [0, 1] and
rejects values outside the scale.

The reranker separates the groups far better than keyword scores. Every unanswerable and near-miss
question scored at most 2.27, while all exact, multi-source and keyword questions and 1 of 8
paraphrases scored 2.38 or higher. Through the real grounded path:

| Minimum score (reranker) | Answer rate | Source recall | Citation precision | False answers | Paraphrases answered |
|---|---|---|---|---|---|
| 0.625 (2.5) | 0.59 | 0.55 | 1.00 | 0.00 | 0 of 8 |
| 0.5 (2.0) | 0.91 | 0.82 | 0.83 | 0.27 | 6 of 8 |
| Lexical baseline (0.5) | 0.50 | 0.45 | 1.00 | 0.00 | 0 of 8 |

At 0.625 the adapter passes every committed guard and beats the baseline. Paraphrases and
near-misses overlap in score (1.93 to 2.50 against 1.63 to 2.27), so no single cutoff recovers
paraphrases without answering some questions that should abstain. The cutoff was picked on the
same 33 questions it is scored on, over a 24-chunk synthetic corpus, so it is an indication and
not a tuned setting. The free plan is for evaluation; production use needs the paid plan.

## Consequences

- `retrieval.adapter: azure-ai-search` stays refused by `create_grounded_executor`. The semantic
  score now passes the baseline's guards, so the remaining work is configuration: the retrieval
  mode, score mapping, minimum score, retrieval limit and attempt count become validated settings,
  and the choice of cutoff is revisited on a larger, more realistic question set.
- The shared scope suite still runs over both providers against a fake that interprets only the
  filter this adapter emits. The live isolation check is the proof against the real service and
  must be rerun after any change to the filter, the index definition or the service API version.
- The Free tier is for this evaluation only (one per subscription, 50 MB, no SLA).
