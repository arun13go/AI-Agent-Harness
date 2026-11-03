"""Route grounded responses from deterministic evidence quality signals."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import (
    AnswerabilityDecision,
    AnswerabilityReason,
    AnswerabilityRoute,
    Citation,
    EvidenceFlag,
    RetrievedItem,
)

_INSUFFICIENT_ROUTES = {
    AnswerabilityRoute.CLARIFY,
    AnswerabilityRoute.CAVEAT,
    AnswerabilityRoute.ABSTAIN,
    AnswerabilityRoute.ESCALATE,
}


class DeterministicAnswerabilityPolicy:
    """Choose a safe route from explicit evidence metadata and thresholds."""

    def __init__(
        self,
        *,
        minimum_score: float,
        insufficient_evidence_route: AnswerabilityRoute,
        maximum_retrieval_attempts: int,
    ) -> None:
        if not 0 <= minimum_score <= 1:
            raise InputValidationError("Minimum evidence score must be between zero and one")
        if insufficient_evidence_route not in _INSUFFICIENT_ROUTES:
            raise InputValidationError("Insufficient evidence requires a safe terminal route")
        if maximum_retrieval_attempts < 0:
            raise InputValidationError("Maximum retrieval attempts cannot be negative")
        self._minimum_score = minimum_score
        self._insufficient_evidence_route = insufficient_evidence_route
        self._maximum_retrieval_attempts = maximum_retrieval_attempts

    def assess(
        self,
        *,
        evidence: Sequence[RetrievedItem],
        now: datetime,
        retrieval_attempt: int,
    ) -> AnswerabilityDecision:
        """Return answer, retry, or a safe terminal route without inspecting payload text."""

        if now.tzinfo is None or now.utcoffset() is None:
            raise InputValidationError("Answerability time must be timezone-aware")
        if retrieval_attempt < 0:
            raise InputValidationError("Retrieval attempt cannot be negative")
        if any(EvidenceFlag.MALICIOUS in item.evidence_flags for item in evidence):
            return self._decision(AnswerabilityRoute.ABSTAIN, AnswerabilityReason.MALICIOUS)
        if any(EvidenceFlag.CONFLICTING in item.evidence_flags for item in evidence):
            return self._decision(AnswerabilityRoute.CLARIFY, AnswerabilityReason.CONFLICTING)
        if not evidence:
            return self._insufficient(AnswerabilityReason.MISSING, retrieval_attempt)

        fresh = [item for item in evidence if item.source.fresh_until > now]
        if not fresh:
            return self._insufficient(AnswerabilityReason.STALE, retrieval_attempt)
        sufficient = [item for item in fresh if item.score >= self._minimum_score]
        if not sufficient:
            return self._insufficient(AnswerabilityReason.WEAK, retrieval_attempt)

        return AnswerabilityDecision(
            route=AnswerabilityRoute.ANSWER,
            reasons=(AnswerabilityReason.SUFFICIENT,),
            citations=tuple(
                Citation(
                    source_id=item.source.source_id,
                    source_version=item.source.source_version,
                    structural_path=item.source.structural_path,
                )
                for item in sufficient
            ),
        )

    def _insufficient(
        self,
        reason: AnswerabilityReason,
        retrieval_attempt: int,
    ) -> AnswerabilityDecision:
        if retrieval_attempt < self._maximum_retrieval_attempts:
            return self._decision(AnswerabilityRoute.RETRIEVE_AGAIN, reason)
        return self._decision(self._insufficient_evidence_route, reason)

    @staticmethod
    def _decision(
        route: AnswerabilityRoute,
        reason: AnswerabilityReason,
    ) -> AnswerabilityDecision:
        return AnswerabilityDecision(route=route, reasons=(reason,))
