# ADR 0023: Azure AI Search Retrieval Adapter Behind a Backend Port

- **Status:** Accepted
- **Date:** 4 November 2026
- **Builds on:** [ADR 0019](0019-local-grounded-execution-path.md), [ADR 0021](0021-caller-authorization-labels.md), [ADR 0022](0022-retrieval-relevance-baseline.md)

## Purpose and scope

The reference retrieval adapter for Azure AI Search, built and tested locally before any cloud
resource exists. This ADR covers the provider, the query filter and the index document shape. It
does not provision a service, add the Azure SDK, wire the adapter into configuration, or claim
that retrieval quality improves; the last needs a live index scored with `eval-retrieval`.

## Decision

- **Backend port.** `AzureSearchRetrievalProvider` depends on a one-method `SearchBackend`
  (`search(text, odata_filter, top)`), not on the SDK. A real backend wraps the SDK later with
  Entra authentication; tests use a fake. The Azure SDK is therefore not a dependency yet.
- **Scope is enforced twice.** The provider builds the filter from validated inputs: tenant equals
  the caller's tenant, and every label on a document is among the caller's labels (`all` over the
  collection), so unlabeled documents are visible to the tenant and a document needing an extra
  label is hidden. This matches the in-memory provider. The provider then re-checks every returned
  document and raises `AuthorizationError`, without echoing the document, if the service returned
  anything out of scope. A wrong index definition or a backend defect fails closed.
- **No string splicing of untrusted values.** Tenant and labels pass the existing validators,
  whose patterns exclude quotes, commas, spaces and control characters, and values are quoted as
  OData literals as a second layer. Only `tenantScope` and `authorizationLabels` are filterable.
- **Scores are mapped, not trusted.** Service scores are unbounded and not comparable across
  queries, but answerability needs [0, 1]. The provider requires an explicit monotonic mapping;
  `saturating_score(half_point)` is the first. It is not a probability. The half point and the
  answerability threshold must be chosen with `eval-retrieval` against a live index, never by
  intuition. Zero scores are dropped and ties break by identifier, so ordering is deterministic.
- **Untrusted output.** Every field is type- and size-checked. Malformed, duplicate or excess
  results raise `RetrievalError` with a generic message. The provider stamps `retrieved_at` and
  the `untrusted-retrieved-content` label itself, and never stores either in the index.
- **Index shape.** `INDEX_FIELDS` declares the chunk identifier as the key (letters, digits,
  `_`, `-`, `=`, which the chunk identity already satisfies), searchable content, lineage,
  freshness, tenant, labels and evidence flags. `evidence_to_document` is the only writer.

## Consequences

- The shared scope behavior runs over both providers in one test module. It uses a fake that
  interprets only the filter this adapter emits and rejects any other, so changing the filter
  forces a test update. The fake cannot prove the real service evaluates the filter the same way.
  The first live run must verify tenant and label isolation with real documents before the
  adapter is enabled, and that evidence will be recorded here.
- Keyword search only. Vector, hybrid and semantic ranking, which the baseline shows are needed
  for paraphrases, are a later change that needs embeddings and extra cost approval.
- `retrieval.adapter: azure-ai-search` stays refused by `create_grounded_executor` until a real
  backend, the validated threshold settings and the live isolation check exist.
- Filters use `search.in` over a canonical, de-duplicated label list capped at 64 labels, so the
  filter stays well inside service limits.
