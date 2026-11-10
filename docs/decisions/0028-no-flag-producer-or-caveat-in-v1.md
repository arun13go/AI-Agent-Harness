# ADR 0028: No Evidence-Flag Producer or `caveat` Route in v1

- **Status:** Accepted
- **Date:** 10 November 2026
- **Builds on:** [ADR 0019](0019-local-grounded-execution-path.md), [ADR 0027](0027-grounded-http-hosting.md)

## Purpose and scope

Evidence can carry `conflicting` and `malicious` flags, and the answerability policy routes them
to clarification and abstention. Nothing in the harness sets those flags, and the `caveat` route
(answer with a stated warning) is refused. This ADR decides not to build a flag producer or the
`caveat` route for v1.

## Decision

- **No built-in flag producer.** The harness will not detect conflicts between sources or hostile
  text. The routing for flagged evidence stays, so a retrieval adapter or an operator-supplied
  step that sets the flags is honored and tested.
- **No `caveat` route in v1.** The executor keeps refusing it at composition time. Conflicts that a
  producer might later flag already route to `clarify`, a safe fixed response.
- **Reason.** Reliable conflict and injection detection is a product in its own right. A simple
  rule would miss subtle cases and flag harmless ones, adding code, tuning and false assurance to a
  template meant to stay small. The controls that matter here do not depend on it: retrieved text
  is delivered only as labeled untrusted data, scope is enforced on every item, weak, stale and
  missing evidence abstain, and the answer is limited to the supplied evidence.

## Consequences

- **Phase 2 exit evidence is narrowed.** Weak, stale, missing and sufficient evidence follow the
  correct route through the grounded path. Conflicting and malicious routing is proven for flags
  supplied to the policy, not for automatic detection.
- **Known limit.** Two sources that disagree can both reach the model, which may state one answer
  without a warning. Users of the template who need detection must add a producer.
- **Revisit** if a pilot shows conflicting sources are a real problem, or when Phase 4 evaluation
  can measure a producer's precision and recall.
