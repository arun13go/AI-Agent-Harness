# ADR 0031: Lightweight by Design

- **Status:** Accepted
- **Date:** 10 November 2026
- **Builds on:** [ADR 0001](0001-maf-reference-runtime.md)

## Decision

Lightweight is a core requirement of the harness, equal in rank to its governance controls. A
customer must be able to clone, understand, deploy and extend it without carrying machinery they
do not use. Mandatory governance controls are never traded for lightness.

## Rules

1. **Less code.** Prefer a released SDK or platform capability over our own code. Add code only for
   a need stated in an ADR. Reserved or speculative options are refused at load, not half-built.
   Delete code that is dead or duplicated in the same change that makes it so.
2. **Fewer parts.** One Azure service before two, one container before several, one small port
   before a framework. A new service, dependency, flag or abstraction needs a written reason.
3. **Small default footprint.** `azd up` deploys only what the quickstart needs. Every other Azure
   resource is opt-in and off by default.
4. **Short, clear documents.** An ADR fits about one page: decision, reason, evidence, limits. Docs
   say what it does, how to run it, and what it does not do. Work logs go to `.local/logs/`, not
   docs. Replies and pull-request text are brief.
5. **Clarity is not optional.** Brevity must not drop a decision, a limit or the evidence for a
   claim.

## Applying it

- Each ADR has a short "Not built" list, so deferrals are visible.
- Each phase gate asks what was deleted or deliberately left out, as well as what was added.
- Earlier code is reviewed against these rules at phase gates, not rewritten speculatively.
