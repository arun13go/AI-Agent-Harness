# ADR 0022: Retrieval Relevance Baseline and Regression Guards

- **Status:** Accepted
- **Date:** 4 November 2026
- **Builds on:** [ADR 0020](0020-local-lexical-retrieval-and-corpus-loading.md)

## Purpose and scope

Relevance was previously judged by eye. This ADR adds the first `evals/` artifact: a versioned,
deterministic question set with expected sources, a runner that scores the real grounded path
without calling a model, and regression guards. It measures retrieval and routing only. It does
not judge answer text, groundedness of a generated answer, latency or cost, which belong to the
evaluation phase and need model-judged metrics.

## Decision

- **Dataset** (`evals/grounded-relevance/`): a small fictional operations handbook (8 Markdown
  files, 24 chunks) and 33 questions in six categories: `exact` (the question reuses the
  document's words), `paraphrase`, `multi-source`, `short` (keyword queries), `unanswerable`
  (nothing relevant) and `near-miss` (shares vocabulary with the corpus but asks for a fact it
  lacks). Each question states the route a correct system takes and the sources it must cite. The
  corpus and questions were written before the scorer was run on them, so they are not tuned to it.
- **Runner** (`eval-retrieval`): sends each question through the composed `GroundedExecutor` with a
  stub in place of the model, so retrieval, scope checks, answerability and context planning are
  exercised exactly as in `grounded-run`. Time, identities and ids are fixed, so a run is
  deterministic and its report names questions by id, never by text.
- **Metrics**, overall and per category: `answer_rate` (answerable questions answered),
  `source_recall` (all expected sources cited), `citation_precision` (cited sources that were
  expected), `route_accuracy`, and `false_answer_rate` (unanswerable questions answered).
- **Guards** (`thresholds.yaml`): the `false_answer_rate` rules are fixed at zero because answering
  a question the corpus cannot answer is a safety failure. Every other rule is a floor set from the
  first measured run and fails only when behavior gets worse. They are regression guards, not
  quality targets. A rule on a category with no applicable questions fails instead of passing.
- **Exit codes:** `eval-retrieval` exits 0 when the guards hold, 1 when one fails (the report is
  still printed), and 2 for configuration or input errors.

## Baseline (lexical scorer, minimum score 0.5)

| Category | Questions | Answered / expected | Notes |
|---|---|---|---|
| exact | 8 | 7 of 8 | one miss: "How many days do customers have to pay an invoice?" |
| paraphrase | 8 | 0 of 8 | shared words are too few for lexical matching |
| multi-source | 2 | 1 of 2 | the answered one cited only one of its two sources |
| short | 4 | 3 of 4 | `backup retention` abstained |
| unanswerable | 6 | 6 of 6 abstained | |
| near-miss | 5 | 5 of 5 abstained | |

Overall: answer rate 0.50, source recall 0.45, citation precision 1.0, false-answer rate 0.0.

**Threshold sweep.** Lowering the minimum score trades safety for recall, and the trade is poor:

| Minimum score | Answer rate | Source recall | Unanswerable questions answered |
|---|---|---|---|
| 0.5 (default) | 0.50 | 0.45 | 0 of 11 |
| 0.4 | 0.59 | 0.50 | 3 of 11 (Sev5, queue backup, incident commander tonight) |
| 0.3 | 0.68 | 0.59 | 6 of 11 |
| 0.1 | 1.00 | 0.86 | 7 of 11 |

Going from 0.5 to 0.4 gains nine points of answer rate and immediately answers questions it must
refuse. The 0.5 default is the highest-safety setting that still answers about half of the
answerable questions, so it stays. A guard test confirms the gate fails at 0.1.

## Consequences

- A lexical scorer cannot separate a near-miss from a real answer, so tuning its threshold cannot
  fix recall. Closing the gap needs semantic retrieval (vectors or hybrid) and a model-judged
  groundedness metric, which supports the Azure AI Search adapter and the evaluation phase.
- The same dataset and runner can score the Azure adapter, so improvement or regression is
  measured instead of argued. New retrieval strategies should be added to this comparison.
- The guards protect a small, synthetic set. 33 questions over one corpus is a smoke test of
  routing behavior, not evidence of production quality, and it will not catch every regression.
- The dataset is deliberately easy to extend: add questions or documents, rerun, and raise the
  floors only with a reason recorded in the change.
