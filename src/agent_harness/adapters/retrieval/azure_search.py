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
    validate_tenant_scope,
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
CORPUS_FIELD = "corpusId"

# Names, types and attributes of the index fields this adapter reads and writes. The key is the
# chunk identifier. Only the key and the three scope fields are filterable, and only the key is
# sortable, which lets ingestion page through a corpus in a stable order.
INDEX_FIELDS: tuple[Mapping[str, Any], ...] = (
    {"name": "id", "type": "Edm.String", "key": True, "filterable": True, "sortable": True},
    {"name": "content", "type": "Edm.String", "searchable": True},
    {"name": "sourceId", "type": "Edm.String"},
    {"name": "sourceVersion", "type": "Edm.String"},
    {"name": "structuralPath", "type": "Edm.String"},
    {"name": "ingestedAt", "type": "Edm.DateTimeOffset"},
    {"name": "freshUntil", "type": "Edm.DateTimeOffset"},
    {"name": TENANT_FIELD, "type": "Edm.String", "filterable": True},
    {"name": LABELS_FIELD, "type": "Collection(Edm.String)", "filterable": True},
    {"name": CORPUS_FIELD, "type": "Edm.String", "filterable": True},
    {"name": "evidenceFlags", "type": "Collection(Edm.String)"},
)

_DOCUMENT_KEY = re.compile(r"^[A-Za-z0-9_=-]{1,1024}$")
_CORPUS_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_TEXT = 4096
# The label rule is enforced after the service ranks, so ask for more than the caller needs.
_OVERFETCH = 5
_MIN_FETCH = 50
_MAX_FETCH = 1000
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


def linear_score(maximum: float) -> Callable[[float], float]:
    """Map a score in [0, maximum] linearly to [0, 1], rejecting anything outside that range.

    Suits scores with a documented scale, such as the semantic reranker's 0 to 4.
    """

    if not math.isfinite(maximum) or maximum <= 0.0:
        raise InputValidationError("Score maximum must be a positive finite number")

    def mapper(raw: float) -> float:
        if not 0.0 <= raw <= maximum:
            raise RetrievalError("Search score is outside the expected range")
        return raw / maximum

    return mapper


def build_scope_filter(identity: IdentityScope, authorization_labels: Sequence[str]) -> str:
    """Return the OData pre-filter for the caller's tenant and labels.

    The service cannot express "every label on the document is held by the caller", because
    inside ``all`` it accepts only negated tests. The filter is therefore a safe superset: the
    tenant matches, and the document is unlabeled or shares at least one label with the caller.
    The provider enforces the exact rule on the results.
    """

    validate_identity_scope(identity)
    labels = validate_authorization_labels(authorization_labels)
    if labels:
        allowed = _literal(",".join(labels))
        label_clause = (
            f"(not {LABELS_FIELD}/any() or {LABELS_FIELD}/any(l: search.in(l, {allowed}, ',')))"
        )
    else:
        label_clause = f"not {LABELS_FIELD}/any()"
    return f"{TENANT_FIELD} eq {_literal(identity.tenant_scope)} and {label_clause}"


def validate_corpus_id(corpus_id: str) -> str:
    """Return a corpus identifier that is safe to store, filter on and show in reports."""

    if not _CORPUS_ID.fullmatch(corpus_id):
        raise InputValidationError(
            "Corpus ID must be 1 to 64 lowercase letters, digits or dashes, starting with a "
            "letter or digit"
        )
    return corpus_id


def build_corpus_filter(tenant_scope: str, corpus_id: str) -> str:
    """Return the OData filter selecting exactly one corpus of one tenant."""

    validate_tenant_scope(tenant_scope)
    return f"{TENANT_FIELD} eq {_literal(tenant_scope)} and {CORPUS_FIELD} eq " + _literal(
        validate_corpus_id(corpus_id)
    )


def build_id_filter(document_ids: Sequence[str]) -> str:
    """Return the OData filter selecting documents by key."""

    if not document_ids or any(not _DOCUMENT_KEY.fullmatch(value) for value in document_ids):
        raise InputValidationError("Document IDs must be non-empty search document keys")
    return f"search.in(id, {_literal(','.join(document_ids))}, ',')"


def evidence_to_document(item: RetrievedItem, *, corpus_id: str | None = None) -> dict[str, Any]:
    """Convert evidence to the index document that ``AzureSearchRetrievalProvider`` reads back.

    ``corpus_id`` records which ingested corpus owns the chunk so a later sync can find the chunks
    it is responsible for. Retrieval does not use it.
    """

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
        CORPUS_FIELD: None if corpus_id is None else validate_corpus_id(corpus_id),
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
        fetch = min(_MAX_FETCH, max(limit * _OVERFETCH, _MIN_FETCH))
        try:
            documents = await self._backend.search(text=query, odata_filter=odata_filter, top=fetch)
        except HarnessError:
            raise
        except Exception as error:
            raise RetrievalError("Search backend request failed") from error
        if len(documents) > fetch:
            raise RetrievalError("Search backend returned more results than requested")
        items: dict[str, RetrievedItem] = {}
        for document in documents:
            item = self._to_item(document, retrieved_at)
            labels = frozenset(item.authorization_labels)
            # The pre-filter guarantees the tenant and, for labeled documents, at least one shared
            # label. A hit that breaks either is a backend or index defect, not a ranking detail.
            if item.tenant_scope != identity.tenant_scope or (
                labels and labels.isdisjoint(allowed)
            ):
                raise AuthorizationError("Search returned evidence outside the caller's scope")
            if item.item_id in items:
                raise RetrievalError("Search returned duplicate evidence identifiers")
            if labels.issubset(allowed):
                items[item.item_id] = item
        ranked = sorted(
            (item for item in items.values() if item.score > 0.0),
            key=lambda item: (-item.score, item.item_id),
        )
        return tuple(ranked[:limit])

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
