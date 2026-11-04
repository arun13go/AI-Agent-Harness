"""Provide deterministic tenant- and authorization-scoped retrieval for local use and tests."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from agent_harness.core.contracts import RelevanceScorer
from agent_harness.core.errors import InputValidationError, RetrievalError
from agent_harness.core.governance import validate_authorization_labels, validate_identity_scope
from agent_harness.core.types import IdentityScope, RetrievedItem


class InMemoryRetrievalProvider:
    """Filter immutable evidence by scope, then order it deterministically.

    Without a scorer, items keep the scores they were created with. With a scorer, scores come
    from the question, items with no relevance are dropped, and the scorer sees only evidence the
    caller is already authorized to read.
    """

    def __init__(
        self,
        items: Sequence[RetrievedItem],
        *,
        clock: Callable[[], datetime] | None = None,
        scorer: RelevanceScorer | None = None,
    ) -> None:
        self._items = tuple(items)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._scorer = scorer

    async def retrieve(
        self,
        *,
        identity: IdentityScope,
        authorization_labels: Sequence[str],
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]:
        """Return only evidence allowed for the caller's tenant and labels."""

        validate_identity_scope(identity)
        if not query.strip() or len(query) > 2048:
            raise InputValidationError("Retrieval query must contain 1 to 2048 characters")
        if not 1 <= limit <= 100:
            raise InputValidationError("Retrieval limit must be between 1 and 100")
        allowed_labels = frozenset(validate_authorization_labels(authorization_labels))
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise InputValidationError("Retrieval time must be timezone-aware")
        eligible = [
            item
            for item in self._items
            if item.tenant_scope == identity.tenant_scope
            and frozenset(item.authorization_labels).issubset(allowed_labels)
        ]
        scored = self._score(query, eligible)
        ranked = sorted(scored, key=lambda item: (-item.score, item.item_id))
        return tuple(
            replace(
                item,
                retrieved_at=retrieved_at,
                source_label="untrusted-retrieved-content",
            )
            for item in ranked[:limit]
        )

    def _score(self, query: str, eligible: list[RetrievedItem]) -> list[RetrievedItem]:
        if self._scorer is None:
            return eligible
        scores = tuple(self._scorer.score(query, eligible))
        if len(scores) != len(eligible) or any(
            not math.isfinite(score) or not 0.0 <= score <= 1.0 for score in scores
        ):
            raise RetrievalError("Relevance scorer returned invalid scores")
        return [
            replace(item, score=score)
            for item, score in zip(eligible, scores, strict=True)
            if score > 0.0
        ]
