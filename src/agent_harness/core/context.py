"""Compose model context from instructions, history, and evidence within a hard token budget."""

from __future__ import annotations

import re
from collections.abc import Sequence

from agent_harness.core.contracts import TokenEstimator
from agent_harness.core.errors import ContextBudgetError, InputValidationError
from agent_harness.core.types import (
    ContextPlan,
    ContextRoute,
    EvidenceFlag,
    Message,
    RetrievedItem,
)

_MESSAGE_OVERHEAD_TOKENS = 4
_UNSAFE_HEADER_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class ConservativeTokenEstimator:
    """Overcount deliberately: one token per three UTF-8 bytes, rounded up."""

    def count(self, text: str) -> int:
        return -(-len(text.encode("utf-8")) // 3)


class DeterministicContextPolicy:
    """Keep mandatory content, newest history, and best-ranked evidence that fit the budget.

    Instructions and the current request are mandatory and never trimmed. After them, evidence may
    use up to ``evidence_fraction`` of what remains, ranked by score; history then uses the rest as
    a contiguous run of the newest turns. Anything left out is reported, never silently shortened.
    """

    def __init__(
        self,
        *,
        estimator: TokenEstimator | None = None,
        evidence_fraction: float = 0.6,
        maximum_evidence_items: int = 20,
    ) -> None:
        if not 0 < evidence_fraction <= 1:
            raise InputValidationError(
                "Evidence fraction must be greater than zero and at most one"
            )
        if not 1 <= maximum_evidence_items <= 100:
            raise InputValidationError("Maximum evidence items must be between 1 and 100")
        self._estimator = estimator or ConservativeTokenEstimator()
        self._evidence_fraction = evidence_fraction
        self._maximum_evidence_items = maximum_evidence_items

    def build(
        self,
        *,
        instructions: Sequence[Message],
        history: Sequence[Message],
        evidence: Sequence[RetrievedItem],
        maximum_tokens: int,
    ) -> ContextPlan:
        """Return the bounded message list and handles for evidence or history left out."""

        if maximum_tokens < 1:
            raise InputValidationError("Context budget must be positive")
        _validate_instructions(instructions)
        _validate_history(history)

        current = history[-1]
        mandatory = self._cost((*instructions, current))
        if mandatory > maximum_tokens:
            raise ContextBudgetError(
                "Instructions and the current request exceed the context budget"
            )
        remaining = maximum_tokens - mandatory

        ranked, rejected = self._rank_evidence(evidence)
        evidence_budget = int(remaining * self._evidence_fraction)
        evidence_messages: list[Message] = []
        included: list[str] = []
        omitted: list[str] = []
        evidence_used = 0
        for item in ranked:
            message = _evidence_message(item)
            cost = self._cost((message,))
            if len(included) < self._maximum_evidence_items and evidence_used + cost <= (
                evidence_budget
            ):
                evidence_messages.append(message)
                included.append(item.item_id)
                evidence_used += cost
            else:
                omitted.append(item.item_id)

        older = history[:-1]
        history_budget = remaining - evidence_used
        kept: list[Message] = []
        history_used = 0
        for message in reversed(older):
            cost = self._cost((message,))
            if history_used + cost > history_budget:
                break
            kept.append(message)
            history_used += cost
        kept.reverse()

        omitted_history = len(older) - len(kept)
        return ContextPlan(
            messages=(*instructions, *kept, *evidence_messages, current),
            estimated_tokens=mandatory + evidence_used + history_used,
            included_evidence_ids=tuple(included),
            omitted_evidence_ids=tuple(omitted),
            rejected_evidence_ids=tuple(rejected),
            omitted_history_count=omitted_history,
            route=ContextRoute.TRIMMED if omitted or omitted_history else ContextRoute.FITS,
        )

    def _cost(self, messages: Sequence[Message]) -> int:
        return sum(
            self._estimator.count(message.content) + _MESSAGE_OVERHEAD_TOKENS
            for message in messages
        )

    @staticmethod
    def _rank_evidence(
        evidence: Sequence[RetrievedItem],
    ) -> tuple[list[RetrievedItem], list[str]]:
        rejected: set[str] = set()
        candidates: dict[str, RetrievedItem] = {}
        for item in sorted(evidence, key=lambda candidate: (-candidate.score, candidate.item_id)):
            if EvidenceFlag.MALICIOUS in item.evidence_flags or not _is_citable(item):
                rejected.add(item.item_id)
            else:
                candidates.setdefault(item.item_id, item)
        # An identifier is rejected if any copy is unsafe, so a safe duplicate cannot launder it.
        ranked = [item for item in candidates.values() if item.item_id not in rejected]
        return ranked, sorted(rejected)


def _is_citable(item: RetrievedItem) -> bool:
    """Reject items whose lineage could break the single-line evidence header or be empty."""

    fields = (
        item.item_id,
        item.source.source_id,
        item.source.source_version,
        item.source.structural_path,
    )
    return bool(item.content.strip()) and not any(
        not value or _UNSAFE_HEADER_CHARACTERS.search(value) for value in fields
    )


def _evidence_message(item: RetrievedItem) -> Message:
    # The label is always reassigned: an item cannot promote itself by claiming to be trusted.
    header = (
        f"evidence_id={item.item_id} source={item.source.source_id} "
        f"version={item.source.source_version} path={item.source.structural_path}"
    )
    return Message("user", f"{header}\n{item.content}", "untrusted-retrieved-content")


def _validate_instructions(instructions: Sequence[Message]) -> None:
    for message in instructions:
        if message.role != "system" or message.source_label != "trusted-instruction":
            raise InputValidationError("Instructions must be trusted system messages")


def _validate_history(history: Sequence[Message]) -> None:
    if not history:
        raise InputValidationError("History must end with the current user request")
    for message in history:
        if message.role == "system" or message.source_label == "trusted-instruction":
            raise InputValidationError("History cannot carry instructions")
    if history[-1].role != "user":
        raise InputValidationError("History must end with the current user request")
