# ADR 0018: Bounded Context Plans With Reported Omissions

- **Status:** Accepted
- **Date:** 4 November 2026
- **Implements:** CTX-01, CTX-02, CTX-04, CTX-05 and CTX-06 of the [pattern catalogue](../delivery/pattern-catalog.md)

## Purpose and scope

Defines how the harness composes model input from instructions, conversation history and retrieved
evidence within a token budget, and what it reports when something does not fit. It covers the
framework-neutral policy only. Prompt wording, summarization and the Azure AI Search adapter are
out of scope.

## Decision

- **The protocol returns a plan, not a bare message list.** `ContextPolicy.build(...)` replaces the
  unimplemented `build_messages(...)` and returns a `ContextPlan` with the messages, the estimated
  token count, included, omitted and rejected evidence identifiers, the number of omitted history
  turns, and a route (`fits` or `trimmed`). Callers can audit counts and fetch omitted evidence by
  handle without the policy copying payloads anywhere.
- **Mandatory content is never trimmed.** Instructions and the current request always stay, in
  order. If they alone exceed the budget the policy raises `ContextBudgetError`
  (`context_budget_exceeded`, a subclass of `ExecutionLimitError`) with a message that contains no
  request content. Failing visibly beats answering a shortened question.
- **The budget is hard relative to an injected `TokenEstimator`.** The default overcounts on
  purpose (one token per three UTF-8 bytes, plus four tokens per message). A real tokenizer can be
  injected, and callers should leave margin for estimator error and for output tokens.
- **Allocation is deterministic.** After mandatory content, evidence may use up to
  `evidence_fraction` (default 0.6) of the remaining budget, best score first, skipping items that
  do not fit and capped at `maximum_evidence_items` (default 20). History then takes what remains as
  a contiguous run of the newest turns, so there are no gaps in a conversation.
- **Evidence is data, always.** Every evidence message is a `user` message labeled
  `untrusted-retrieved-content`, whatever the item claims. History and evidence cannot become
  instructions: instructions must be trusted `system` messages, and history may not contain
  `system` messages or the `trusted-instruction` label.
- **Unsafe evidence is rejected, not trimmed.** Evidence flagged malicious, blank, or with lineage
  fields that are empty or contain control characters (which could forge the one-line evidence
  header) is excluded and reported as rejected. If any copy of an identifier is unsafe, all copies
  are rejected, so a safe duplicate cannot launder it.
- **Omitted evidence is a handle.** Evidence left out for budget reasons is listed by identifier in
  rank order, so a later step can retrieve it by handle instead of silently losing it (CTX-04).

## Consequences

- The MAF executor needs no change: it already treats only `trusted-instruction` messages as
  instructions and wraps untrusted messages as labeled JSON. A test sends a planned context through
  `MafAgentExecutor` and checks that hostile evidence text never reaches the instructions.
- Profiles currently set `context.compaction: summarize`, but this policy only truncates, and the
  MAF runtime applies its own truncation strategy. Summarization needs a model call and is not
  deterministic, so it remains a separate, flagged decision. Until then `summarize` should not be
  read as summarizing.
- Whole-message trimming can leave unused budget. That is accepted to keep messages intact.
- A grounded application path must call the answerability policy first, then this policy, and emit
  audit events from the plan's counts.
