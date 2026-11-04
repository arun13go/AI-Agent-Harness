# ADR 0020: Local Lexical Retrieval and Corpus Loading

- **Status:** Accepted
- **Date:** 4 November 2026
- **Builds on:** [ADR 0017](0017-deterministic-chunk-identity.md), [ADR 0019](0019-local-grounded-execution-path.md)

## Purpose and scope

Makes the local grounded path respond to the question: a deterministic relevance scorer for the
in-memory provider, a loader that turns a Markdown directory into scoped evidence, and a
`grounded-run` command. It is a development and conformance aid, not the production retrieval
strategy, which remains Azure AI Search ([ADR 0007](0007-azure-ai-search-reference-retrieval.md)).

## Decision

- **Scoring.** `IdfCoverageScorer` scores a candidate as the share of the question's IDF weight
  that the candidate's text contains, in `[0, 1]`. 1.0 means every content word of the question is
  present. Words are NFKC-normalized, case-folded, stripped of stopwords and one-character tokens,
  and folded for simple plurals; the same function runs on questions and evidence.
- **Statistics come only from authorized candidates.** The provider filters by tenant and
  authorization labels first and passes only the survivors to the scorer. Document frequencies
  computed over a whole corpus would let scores reveal how often a term appears in other tenants'
  or restricted documents. A test floods other tenants and labels with the question's own words and
  requires every visible score to stay identical. The `RelevanceScorer` contract states this duty.
- **Zero-relevance evidence is dropped.** With a scorer, items scoring 0 are not returned, and
  scorer output that is not one finite value in `[0, 1]` per candidate fails closed with
  `RetrievalError`.
- **Conservative by design.** A question word found nowhere in the candidates still counts against
  every score, so questions about things the corpus does not mention fall under the answerability
  threshold and abstain instead of matching on filler.
- **Corpus loader.** `load_markdown_corpus` treats the directory as untrusted: symbolic links are
  refused, hidden files and directories are skipped, and file count, per-file and total size, and
  UTF-8 validity are bounded. Files load in sorted order. A file's `sourceVersion` is a hash of its
  bytes, so edited content always gets new chunk identifiers (ADR 0017). Freshness is synthetic
  (ingested now, fresh for `--fresh-days`).
- **`grounded-run`.** It validates the profile before creating a model client, so a configuration
  error is never masked by missing Foundry settings. `--corpus-label` is the access label the
  corpus requires and `--caller-label` the label the caller holds. The caller label is a
  development convenience; a host must take labels from authentication.

## Evidence and limits

An informal check against the repository's own decision records (90 chunks, no model, eight
questions) behaved as designed on unanswerable questions (a general-knowledge question returned
nothing; a question about restarting a service scored 0.32 against unrelated text and abstained).
It also showed the cost of the conservative design: two of six answerable questions abstained
because natural-language filler such as "happens" and "small" does not appear in the text, and for
another the best source was not the top hit. This is not an evaluation, and the scorer was not
tuned to these questions. Paraphrase and synonym handling is the job of vector and hybrid
retrieval, and measured quality belongs to the evaluation phase.

## Consequences

- Retrieval over a small local corpus now responds to the question, and the grounded path can be
  demonstrated without Azure. Cost is linear in corpus size per query, which suits development.
- The default minimum score (0.5) is strict for conversational questions. It is a parameter
  (`--minimum-score`) until thresholds move into validated configuration with the Azure adapter.
- The scorer and loader must stay behind the contracts so the Azure AI Search adapter can replace
  them without changing the grounded path.
- The real symbolic-link tests cannot run where the OS forbids creating links; equivalent tests
  that simulate link detection cover the refusal logic everywhere.
