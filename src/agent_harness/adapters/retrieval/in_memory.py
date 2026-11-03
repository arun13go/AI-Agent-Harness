"""Provide deterministic tenant- and authorization-scoped retrieval for conformance tests."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from agent_harness.core.errors import InputValidationError
from agent_harness.core.governance import validate_identity_scope
from agent_harness.core.types import IdentityScope, RetrievedItem

_AUTHORIZATION_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class InMemoryRetrievalProvider:
    """Filter immutable evidence before deterministic score ordering."""

    def __init__(
        self,
        items: Sequence[RetrievedItem],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._items = tuple(items)
        self._clock = clock or (lambda: datetime.now(UTC))

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
        if any(not _AUTHORIZATION_LABEL.fullmatch(label) for label in authorization_labels):
            raise InputValidationError("Authorization label is invalid")

        allowed_labels = frozenset(authorization_labels)
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise InputValidationError("Retrieval time must be timezone-aware")
        eligible = (
            item
            for item in self._items
            if item.tenant_scope == identity.tenant_scope
            and frozenset(item.authorization_labels).issubset(allowed_labels)
        )
        ranked = sorted(eligible, key=lambda item: (-item.score, item.item_id))
        return tuple(
            replace(
                item,
                retrieved_at=retrieved_at,
                source_label="untrusted-retrieved-content",
            )
            for item in ranked[:limit]
        )
