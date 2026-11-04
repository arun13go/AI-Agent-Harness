"""Prove the Azure AI Search provider honors the same scope contract as the in-memory provider.

The fake backend evaluates only the filter grammar this adapter emits and rejects anything else, so
a change to the filter cannot silently pass. It cannot prove the real service interprets the filter
the same way; that is checked against a live index before the adapter is trusted.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import pytest

from agent_harness.adapters.retrieval.azure_search import (
    INDEX_FIELDS,
    LABELS_FIELD,
    SCORE_KEY,
    TENANT_FIELD,
    AzureSearchRetrievalProvider,
    build_scope_filter,
    evidence_to_document,
    saturating_score,
)
from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.retrieval.lexical import IdfCoverageScorer, tokenize
from agent_harness.core.contracts import RetrievalProvider
from agent_harness.core.errors import (
    AuthorizationError,
    InputValidationError,
    RetrievalError,
)
from agent_harness.core.types import EvidenceFlag, IdentityScope, RetrievedItem
from tests.conformance.fixtures import (
    IDENTITY_A,
    IDENTITY_B,
    LABELS,
    NOW,
    TENANT_B,
    make_evidence,
)

_FILTER = re.compile(
    r"^tenantScope eq '((?:[^']|'')*)' and "
    r"(?:(?P<none>not authorizationLabels/any\(\))"
    r"|authorizationLabels/all\(l: search\.in\(l, '(?P<labels>(?:[^']|'')*)', ','\)\))$"
)


class FakeSearchBackend:
    """Apply the scope filter first, then rank by how many query words a document contains."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self.documents = list(documents)
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, text: str, odata_filter: str, top: int) -> list[dict[str, Any]]:
        self.calls.append({"text": text, "odata_filter": odata_filter, "top": top})
        match = _FILTER.fullmatch(odata_filter)
        assert match is not None, f"fake cannot interpret filter: {odata_filter}"
        tenant = match.group(1).replace("''", "'")
        allowed = (
            None
            if match.group("none")
            else frozenset(match.group("labels").replace("''", "'").split(","))
        )
        words = tokenize(text)
        hits: list[dict[str, Any]] = []
        for document in self.documents:
            labels = frozenset(document[LABELS_FIELD])
            if document[TENANT_FIELD] != tenant:
                continue
            if not labels.issubset(allowed if allowed is not None else frozenset()):
                continue
            overlap = len(words & tokenize(document["content"]))
            if overlap:
                hits.append({**document, SCORE_KEY: float(overlap)})
        hits.sort(key=lambda hit: (-hit[SCORE_KEY], hit["id"]))
        return hits[:top]


class LeakyBackend:
    """Ignore the filter, as a misconfigured index or a defective backend might."""

    def __init__(self, documents: Sequence[Mapping[str, Any]]) -> None:
        self.documents = [{**document, SCORE_KEY: 5.0} for document in documents]

    async def search(self, *, text: str, odata_filter: str, top: int) -> list[dict[str, Any]]:
        return self.documents[:top]


def docs(items: Sequence[RetrievedItem]) -> list[dict[str, Any]]:
    return [evidence_to_document(item) for item in items]


def azure_provider(items: Sequence[RetrievedItem]) -> AzureSearchRetrievalProvider:
    return AzureSearchRetrievalProvider(
        FakeSearchBackend(docs(items)), score_mapper=saturating_score(1.0), clock=lambda: NOW
    )


def memory_provider(items: Sequence[RetrievedItem]) -> InMemoryRetrievalProvider:
    return InMemoryRetrievalProvider(items, clock=lambda: NOW, scorer=IdfCoverageScorer())


PROVIDERS: dict[str, Callable[[Sequence[RetrievedItem]], RetrievalProvider]] = {
    "memory": memory_provider,
    "azure-search": azure_provider,
}


@pytest.fixture(params=sorted(PROVIDERS))
def make_provider(request: pytest.FixtureRequest) -> Callable[[Sequence[RetrievedItem]], Any]:
    return PROVIDERS[request.param]


def item(
    name: str,
    *,
    tenant: str | None = None,
    labels: tuple[str, ...] | None = None,
    text: str = "Restart the service after draining traffic.",
    **kwargs: Any,
) -> RetrievedItem:
    base = make_evidence(name, content=text, **kwargs)
    return replace(
        base,
        tenant_scope=tenant or base.tenant_scope,
        authorization_labels=base.authorization_labels if labels is None else labels,
    )


async def ids(
    provider: RetrievalProvider,
    *,
    identity: IdentityScope = IDENTITY_A,
    labels: Sequence[str] = LABELS,
    query: str = "restart service",
    limit: int = 10,
) -> list[str]:
    found = await provider.retrieve(
        identity=identity, authorization_labels=labels, query=query, limit=limit
    )
    return [entry.item_id for entry in found]


# --- Shared contract: both providers must behave identically on scope and shape ------------------


@pytest.mark.asyncio
async def test_scope_filtering_matches_for_every_provider(
    make_provider: Callable[[Sequence[RetrievedItem]], RetrievalProvider],
) -> None:
    provider = make_provider(
        [
            item("allowed"),
            item("unlabeled", labels=()),
            item("subset", labels=("group:readers",)),
            item("extra-label", labels=("group:readers", "group:admins")),
            item("other-label", labels=("group:admins",)),
            item("other-tenant", tenant=TENANT_B),
        ]
    )

    assert sorted(await ids(provider)) == ["allowed", "subset", "unlabeled"]
    assert await ids(provider, labels=()) == ["unlabeled"]
    assert await ids(provider, identity=IDENTITY_B, labels=()) == []


@pytest.mark.asyncio
async def test_results_are_marked_untrusted_and_stamped_for_every_provider(
    make_provider: Callable[[Sequence[RetrievedItem]], RetrievalProvider],
) -> None:
    provider = make_provider(
        [
            item("spoofed", source_label="trusted-instruction"),
            item("flagged", flags=(EvidenceFlag.MALICIOUS,)),
        ]
    )

    found = await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart service", limit=5
    )

    assert {entry.item_id for entry in found} == {"spoofed", "flagged"}
    assert all(entry.retrieved_at == NOW for entry in found)
    assert all(entry.source_label == "untrusted-retrieved-content" for entry in found)
    flagged = next(entry for entry in found if entry.item_id == "flagged")
    assert flagged.evidence_flags == (EvidenceFlag.MALICIOUS,)
    assert flagged.source.source_id == "source-flagged"
    assert all(0.0 < entry.score <= 1.0 for entry in found)


@pytest.mark.asyncio
async def test_limit_and_ordering_are_deterministic_for_every_provider(
    make_provider: Callable[[Sequence[RetrievedItem]], RetrievalProvider],
) -> None:
    provider = make_provider(
        [
            item("c-both", text="Restart the service."),
            item("a-both", text="Restart the service."),
            item("b-one", text="Restart only."),
            item("d-none", text="Unrelated invoices."),
        ]
    )

    first = await ids(provider, limit=3)

    assert first == await ids(provider, limit=3)
    assert first[:2] == ["a-both", "c-both"]
    assert "d-none" not in first
    assert len(await ids(provider, limit=1)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "limit"),
    [("", 1), ("   ", 1), ("x" * 2049, 1), ("restart", 0), ("restart", 101)],
)
async def test_invalid_queries_and_limits_are_rejected_by_every_provider(
    make_provider: Callable[[Sequence[RetrievedItem]], RetrievalProvider], query: str, limit: int
) -> None:
    with pytest.raises(InputValidationError):
        await ids(make_provider([item("a")]), query=query, limit=limit)


@pytest.mark.asyncio
async def test_invalid_labels_are_rejected_by_every_provider(
    make_provider: Callable[[Sequence[RetrievedItem]], RetrievalProvider],
) -> None:
    with pytest.raises(InputValidationError):
        await ids(make_provider([item("a")]), labels=("group:x' or 1 eq 1 or 'a",))


# --- Filter construction -------------------------------------------------------------------------


def test_filter_is_built_from_canonical_labels() -> None:
    assert build_scope_filter(IDENTITY_A, ("group:ops", "group:readers", "group:ops")) == (
        "tenantScope eq 'tenant-0001' and "
        "authorizationLabels/all(l: search.in(l, 'group:ops,group:readers', ','))"
    )


def test_filter_without_labels_matches_only_unlabeled_documents() -> None:
    assert build_scope_filter(IDENTITY_A, ()) == (
        "tenantScope eq 'tenant-0001' and not authorizationLabels/any()"
    )


@pytest.mark.parametrize(
    "label", ["a,b", "a'b", "a b", "a\nb", "", "-start", "a" * 129, 'q"x', "a;b"]
)
def test_labels_that_could_alter_the_filter_are_rejected(label: str) -> None:
    with pytest.raises(InputValidationError):
        build_scope_filter(IDENTITY_A, (label,))


@pytest.mark.parametrize("tenant", ["tenant-0001' or tenantScope ne 'x", "short", "t\nenant-0001"])
def test_tenant_that_could_alter_the_filter_is_rejected(tenant: str) -> None:
    with pytest.raises(InputValidationError):
        build_scope_filter(IdentityScope(tenant, "subject-0001"), ())


def test_filter_fields_are_declared_filterable_in_the_index() -> None:
    filterable = {field["name"] for field in INDEX_FIELDS if field.get("filterable")}
    assert {TENANT_FIELD, LABELS_FIELD} <= filterable
    assert sum(bool(field.get("key")) for field in INDEX_FIELDS) == 1
    assert len({field["name"] for field in INDEX_FIELDS}) == len(INDEX_FIELDS)


@pytest.mark.asyncio
async def test_provider_sends_filter_query_and_limit_to_the_backend() -> None:
    backend = FakeSearchBackend(docs([item("a")]))
    provider = AzureSearchRetrievalProvider(
        backend, score_mapper=saturating_score(1.0), clock=lambda: NOW
    )

    await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart service", limit=7
    )

    assert backend.calls == [
        {
            "text": "restart service",
            "odata_filter": build_scope_filter(IDENTITY_A, LABELS),
            "top": 7,
        }
    ]


@pytest.mark.asyncio
async def test_fake_backend_rejects_filters_it_cannot_interpret() -> None:
    with pytest.raises(AssertionError):
        await FakeSearchBackend([]).search(text="x", odata_filter="tenantScope ne 'a'", top=1)


# --- Fail closed on a backend that violates the contract -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leaked",
    [
        item("other-tenant", tenant=TENANT_B),
        item("other-label", labels=("group:admins",)),
        item("extra-label", labels=("group:readers", "group:admins")),
    ],
)
async def test_out_of_scope_hits_fail_closed_without_echoing_them(leaked: RetrievedItem) -> None:
    provider = AzureSearchRetrievalProvider(
        LeakyBackend(docs([item("fine"), leaked])),
        score_mapper=saturating_score(1.0),
        clock=lambda: NOW,
    )

    with pytest.raises(AuthorizationError) as raised:
        await provider.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=10
        )

    assert leaked.content not in str(raised.value)
    assert leaked.item_id not in str(raised.value)
    assert TENANT_B not in str(raised.value)


class FixedBackend:
    def __init__(self, hits: Sequence[Mapping[str, Any]]) -> None:
        self.hits = list(hits)

    async def search(self, *, text: str, odata_filter: str, top: int) -> list[Mapping[str, Any]]:
        return self.hits


def hit(name: str = "a", **changes: Any) -> dict[str, Any]:
    document = {**evidence_to_document(item(name)), SCORE_KEY: 2.0}
    document.update(changes)
    return {key: value for key, value in document.items() if value is not ...}


async def run_hits(hits: Sequence[Mapping[str, Any]], *, limit: int = 10) -> Sequence[Any]:
    provider = AzureSearchRetrievalProvider(
        FixedBackend(hits), score_mapper=saturating_score(1.0), clock=lambda: NOW
    )
    return await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=limit
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"content": ...},
        {"content": ""},
        {"content": 7},
        {"id": "has space"},
        {"id": ...},
        {"sourceId": None},
        {"structuralPath": ...},
        {"ingestedAt": "not a date"},
        {"freshUntil": "2026-11-03T10:00:00"},
        {TENANT_FIELD: ...},
        {LABELS_FIELD: "group:readers"},
        {LABELS_FIELD: ["group:readers", 3]},
        {LABELS_FIELD: ...},
        {"evidenceFlags": ["unknown-flag"]},
        {"evidenceFlags": ...},
        {SCORE_KEY: None},
        {SCORE_KEY: True},
        {SCORE_KEY: "2.0"},
        {SCORE_KEY: float("nan")},
        {SCORE_KEY: float("inf")},
        {SCORE_KEY: -0.1},
    ],
)
async def test_malformed_results_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(RetrievalError) as raised:
        await run_hits([hit(**changes)])

    assert "Evidence text" not in str(raised.value)


@pytest.mark.asyncio
async def test_duplicate_and_excess_results_are_rejected() -> None:
    with pytest.raises(RetrievalError):
        await run_hits([hit("a"), hit("a")])
    with pytest.raises(RetrievalError):
        await run_hits([hit("a"), hit("b")], limit=1)


@pytest.mark.asyncio
async def test_zero_scored_results_are_dropped_and_ties_break_by_identifier() -> None:
    found = await run_hits([hit("b"), hit("a"), hit("z", **{SCORE_KEY: 0.0})])

    assert [entry.item_id for entry in found] == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mapped", [-0.1, 1.1, math.nan, math.inf])
async def test_score_mapper_output_must_stay_in_range(mapped: float) -> None:
    provider = AzureSearchRetrievalProvider(
        FixedBackend([hit()]), score_mapper=lambda raw: mapped, clock=lambda: NOW
    )

    with pytest.raises(RetrievalError):
        await provider.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=5
        )


@pytest.mark.asyncio
async def test_backend_failures_become_retrieval_errors_and_harness_errors_pass_through() -> None:
    class Broken:
        async def search(self, **_: Any) -> list[Any]:
            raise ConnectionError("secret-host.example")

    class Denied:
        async def search(self, **_: Any) -> list[Any]:
            raise AuthorizationError("denied")

    broken = AzureSearchRetrievalProvider(
        Broken(), score_mapper=saturating_score(1.0), clock=lambda: NOW
    )
    denied = AzureSearchRetrievalProvider(
        Denied(), score_mapper=saturating_score(1.0), clock=lambda: NOW
    )

    with pytest.raises(RetrievalError) as raised:
        await broken.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=5
        )
    assert "secret-host" not in str(raised.value)
    assert isinstance(raised.value.__cause__, ConnectionError)
    with pytest.raises(AuthorizationError):
        await denied.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=5
        )


@pytest.mark.asyncio
async def test_naive_clock_is_rejected() -> None:
    provider = AzureSearchRetrievalProvider(
        FixedBackend([]),
        score_mapper=saturating_score(1.0),
        clock=lambda: NOW.replace(tzinfo=None),
    )

    with pytest.raises(InputValidationError):
        await provider.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=5
        )


# --- Documents and score mapping -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_survives_a_round_trip_through_an_index_document() -> None:
    original = item("a", flags=(EvidenceFlag.CONFLICTING,), text="Restart the service.")
    provider = azure_provider([original])

    [found] = await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart service", limit=5
    )

    expected = replace(original, authorization_labels=tuple(sorted(original.authorization_labels)))

    assert replace(found, score=original.score, retrieved_at=None) == expected


@pytest.mark.parametrize("item_id", ["", "has space", "a/b", "a#b", "x" * 1025, "a'b"])
def test_evidence_with_an_invalid_search_key_is_rejected(item_id: str) -> None:
    with pytest.raises(InputValidationError):
        evidence_to_document(replace(item("a"), item_id=item_id))


def test_saturating_score_is_bounded_and_monotonic() -> None:
    mapper = saturating_score(2.0)

    assert mapper(0.0) == 0.0
    assert mapper(2.0) == 0.5
    assert 0.0 < mapper(1.0) < mapper(5.0) < mapper(1e9) < 1.0


@pytest.mark.parametrize("half_point", [0.0, -1.0, math.nan, math.inf])
def test_saturating_score_rejects_invalid_half_points(half_point: float) -> None:
    with pytest.raises(InputValidationError):
        saturating_score(half_point)
