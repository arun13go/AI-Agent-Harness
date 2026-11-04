"""Retrieve scoped evidence from an Azure AI Search index through a narrow backend port.

The service filters by tenant and authorization labels before ranking. The provider still checks
every returned document, so a wrong index definition or a backend defect fails closed instead of
leaking evidence. The backend port keeps the Azure SDK out of this module and lets the shared
conformance suite run without a cloud resource.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from agent_harness.core.errors import (
    AuthorizationError,
    HarnessError,
    InputValidationError,
    RetrievalError,
)
from agent_harness.core.governance import (
    validate_authorization_labels,
    validate_identity_scope,
)
from agent_harness.core.types import (
    EvidenceFlag,
    IdentityScope,
    RetrievedItem,
    SourceReference,
)

SCORE_KEY = "@search.score"
TENANT_FIELD = "tenantScope"
LABELS_FIELD = "authorizationLabels"

# Names, types and attributes of the index fields this adapter reads and writes. Only the two
# scope fields are filterable, and the key field is the chunk identifier.
INDEX_FIELDS: tuple[Mapping[str, Any], ...] = (
    {"name": "id", "type": "Edm.String", "key": True, "filterable": False},
    {"name": "content", "type": "Edm.String", "searchable": True},
    {"name": "sourceId", "type": "Edm.String"},
    {"name": "sourceVersion", "type": "Edm.String"},
    {"name": "structuralPath", "type": "Edm.String"},
    {"name": "ingestedAt", "type": "Edm.DateTimeOffset"},
    {"name": "freshUntil", "type": "Edm.DateTimeOffset"},
    {"name": TENANT_FIELD, "type": "Edm.String", "filterable": True},
    {"name": LABELS_FIELD, "type": "Collection(Edm.String)", "filterable": True},
    {"name": "evidenceFlags", "type": "Collection(Edm.String)"},
)

_DOCUMENT_KEY = re.compile(r"^[A-Za-z0-9_=-]{1,1024}$")
_MAX_TEXT = 4096
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class SearchBackend(Protocol):
    """Run one keyword search and return raw index documents with their service score.

    The filter is an OData expression built by this module and must be applied by the service
    before ranking. Each result maps index field names to values and carries ``@search.score``.
    """

    async def search(
        self, *, text: str, odata_filter: str, top: int
    ) -> Sequence[Mapping[str, Any]]: ...


def saturating_score(half_point: float) -> Callable[[float], float]:
    """Map a raw non-negative service score into [0, 1) with ``score / (score + half_point)``.

    The raw score is not calibrated across queries, so this mapping is monotonic and bounded but
    not a probability. Choose ``half_point`` from the relevance evaluation, not by intuition.
    """

    if not math.isfinite(half_point) or half_point <= 0.0:
        raise InputValidationError("Score half point must be a positive finite number")
    return lambda raw: raw / (raw + half_point)


def build_scope_filter(identity: IdentityScope, authorization_labels: Sequence[str]) -> str:
    """Return the OData filter that limits results to the caller's tenant and labels.

    A document is visible when every label on it is among the caller's labels, and a document
    with no labels is visible to everyone in the tenant, matching the in-memory provider.
    """

    validate_identity_scope(identity)
    labels = validate_authorization_labels(authorization_labels)
    if labels:
        allowed = _literal(",".join(labels))
        label_clause = f"{LABELS_FIELD}/all(l: search.in(l, {allowed}, ','))"
    else:
        label_clause = f"not {LABELS_FIELD}/any()"
    return f"{TENANT_FIELD} eq {_literal(identity.tenant_scope)} and {label_clause}"


def evidence_to_document(item: RetrievedItem) -> dict[str, Any]:
    """Convert evidence to the index document that ``AzureSearchRetrievalProvider`` reads back."""

    if not _DOCUMENT_KEY.fullmatch(item.item_id):
        raise InputValidationError("Evidence ID is not a valid search document key")
    return {
        "id": item.item_id,
        "content": item.content,
        "sourceId": item.source.source_id,
        "sourceVersion": item.source.source_version,
        "structuralPath": item.source.structural_path,
        "ingestedAt": item.source.ingested_at.astimezone(UTC).isoformat(),
        "freshUntil": item.source.fresh_until.astimezone(UTC).isoformat(),
        TENANT_FIELD: item.tenant_scope,
        LABELS_FIELD: list(item.authorization_labels),
        "evidenceFlags": [flag.value for flag in item.evidence_flags],
    }


class AzureSearchRetrievalProvider:
    """Retrieve evidence for one caller, treating the backend as untrusted."""

    def __init__(
        self,
        backend: SearchBackend,
        *,
        score_mapper: Callable[[float], float],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._score_mapper = score_mapper
        self._clock = clock or (lambda: datetime.now(UTC))

    async def retrieve(
        self,
        *,
        identity: IdentityScope,
        authorization_labels: Sequence[str],
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]:
        """Return evidence the caller may read, ranked by mapped score then identifier."""

        if not query.strip() or len(query) > 2048:
            raise InputValidationError("Retrieval query must contain 1 to 2048 characters")
        if not 1 <= limit <= 100:
            raise InputValidationError("Retrieval limit must be between 1 and 100")
        odata_filter = build_scope_filter(identity, authorization_labels)
        allowed = frozenset(validate_authorization_labels(authorization_labels))
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise InputValidationError("Retrieval time must be timezone-aware")
        try:
            documents = await self._backend.search(text=query, odata_filter=odata_filter, top=limit)
        except HarnessError:
            raise
        except Exception as error:
            raise RetrievalError("Search backend request failed") from error
        if len(documents) > limit:
            raise RetrievalError("Search backend returned more results than requested")
        items: dict[str, RetrievedItem] = {}
        for document in documents:
            item = self._to_item(document, retrieved_at)
            if item.tenant_scope != identity.tenant_scope or not frozenset(
                item.authorization_labels
            ).issubset(allowed):
                raise AuthorizationError("Search returned evidence outside the caller's scope")
            if item.item_id in items:
                raise RetrievalError("Search returned duplicate evidence identifiers")
            items[item.item_id] = item
        ranked = sorted(
            (item for item in items.values() if item.score > 0.0),
            key=lambda item: (-item.score, item.item_id),
        )
        return tuple(ranked)

    def _to_item(self, document: Mapping[str, Any], retrieved_at: datetime) -> RetrievedItem:
        raw_score = document.get(SCORE_KEY)
        if (
            isinstance(raw_score, bool)
            or not isinstance(raw_score, int | float)
            or not math.isfinite(raw_score)
            or raw_score < 0.0
        ):
            raise RetrievalError("Search result has an invalid score")
        score = self._score_mapper(float(raw_score))
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise RetrievalError("Score mapper returned a value outside [0, 1]")
        try:
            return RetrievedItem(
                item_id=_text(document, "id", pattern=_DOCUMENT_KEY),
                content=_text(document, "content", limit=1_000_000),
                score=score,
                source=SourceReference(
                    source_id=_text(document, "sourceId", limit=256),
                    source_version=_text(document, "sourceVersion", limit=256),
                    structural_path=_text(document, "structuralPath", limit=512),
                    ingested_at=_moment(document, "ingestedAt"),
                    fresh_until=_moment(document, "freshUntil"),
                ),
                tenant_scope=_text(document, TENANT_FIELD),
                authorization_labels=tuple(sorted(_strings(document, LABELS_FIELD))),
                retrieved_at=retrieved_at,
                evidence_flags=tuple(
                    EvidenceFlag(flag) for flag in _strings(document, "evidenceFlags")
                ),
                source_label="untrusted-retrieved-content",
            )
        except ValueError as error:
            raise RetrievalError("Search result is malformed") from error


def _literal(value: str) -> str:
    if _CONTROL_CHARACTERS.search(value):
        raise InputValidationError("Filter value contains control characters")
    return "'" + value.replace("'", "''") + "'"


def _text(
    document: Mapping[str, Any],
    name: str,
    *,
    limit: int = _MAX_TEXT,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise RetrievalError("Search result is malformed")
    if pattern is not None and not pattern.fullmatch(value):
        raise RetrievalError("Search result is malformed")
    return value


def _strings(document: Mapping[str, Any], name: str) -> list[str]:
    value: object = document.get(name)
    if not isinstance(value, list | tuple):
        raise RetrievalError("Search result is malformed")
    entries = cast("Sequence[object]", value)
    strings = [entry for entry in entries if isinstance(entry, str)]
    if len(strings) != len(entries):
        raise RetrievalError("Search result is malformed")
    return strings


def _moment(document: Mapping[str, Any], name: str) -> datetime:
    parsed = datetime.fromisoformat(_text(document, name))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RetrievalError("Search result is malformed")
    return parsed
