"""Operate a live Azure AI Search index: load the evaluation corpus and verify scope isolation.

The isolation check writes canary documents for two tenants and several labels into a separate
index, then asks as different callers and requires each to see exactly the documents it is allowed
to. It exists because the fake backend cannot prove the real service evaluates the scope filter
the way the provider assumes. Reports name document identifiers and counts, never content.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from agent_harness.adapters.retrieval.azure_search import (
    AzureSearchRetrievalProvider,
    SearchBackend,
    evidence_to_document,
    saturating_score,
)
from agent_harness.adapters.retrieval.azure_search_rest import (
    AzureSearchRestBackend,
    TokenCredential,
)
from agent_harness.app.retrieval_eval import load_evaluation_corpus
from agent_harness.core.errors import HarnessError
from agent_harness.core.types import IdentityScope, RetrievedItem, SourceReference

TENANT_A = "tenant-canary-a01"
TENANT_B = "tenant-canary-b01"
TENANT_C = "tenant-canary-c01"
_CANARY_QUERY = "zebrafish canary"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SETTLE_ATTEMPTS = 20
_SETTLE_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class _Case:
    name: str
    tenant: str
    labels: tuple[str, ...]
    expected: frozenset[str]


def _canary(item_id: str, tenant: str, labels: tuple[str, ...]) -> RetrievedItem:
    return RetrievedItem(
        item_id=item_id,
        content="Canary note about the zebrafish tank.",
        score=0.0,
        source=SourceReference(
            source_id=f"canary-source-{item_id}",
            source_version="v1",
            structural_path=f"/canary/{item_id}.md#note",
            ingested_at=_NOW,
            fresh_until=_NOW + timedelta(days=36500),
        ),
        tenant_scope=tenant,
        authorization_labels=labels,
    )


CANARY_ITEMS: tuple[RetrievedItem, ...] = (
    _canary("canary-a-public", TENANT_A, ()),
    _canary("canary-a-finance", TENANT_A, ("group:finance",)),
    _canary("canary-a-finance-hr", TENANT_A, ("group:finance", "group:hr")),
    _canary("canary-b-public", TENANT_B, ()),
    _canary("canary-b-finance", TENANT_B, ("group:finance",)),
)

_CASES: tuple[_Case, ...] = (
    _Case("tenant A, no labels", TENANT_A, (), frozenset({"canary-a-public"})),
    _Case(
        "tenant A, finance",
        TENANT_A,
        ("group:finance",),
        frozenset({"canary-a-public", "canary-a-finance"}),
    ),
    _Case(
        "tenant A, finance and hr",
        TENANT_A,
        ("group:finance", "group:hr"),
        frozenset({"canary-a-public", "canary-a-finance", "canary-a-finance-hr"}),
    ),
    _Case("tenant A, hr only", TENANT_A, ("group:hr",), frozenset({"canary-a-public"})),
    _Case("tenant A, unrelated label", TENANT_A, ("group:other",), frozenset({"canary-a-public"})),
    _Case("tenant B, no labels", TENANT_B, (), frozenset({"canary-b-public"})),
    _Case(
        "tenant B, finance",
        TENANT_B,
        ("group:finance",),
        frozenset({"canary-b-public", "canary-b-finance"}),
    ),
    _Case("tenant C, every label", TENANT_C, ("group:finance", "group:hr"), frozenset()),
)


class IndexBackend(SearchBackend, Protocol):
    """A search backend that can also create an index and upload documents."""

    async def create_index(self) -> None: ...

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int: ...


def build_backend(
    *,
    endpoint: str,
    index_name: str,
    credential: TokenCredential | None = None,
    semantic: bool = False,
) -> AzureSearchRestBackend:
    """Create a REST backend that authenticates with the developer's Entra identity."""

    if credential is None:
        from azure.identity import DefaultAzureCredential

        chosen: TokenCredential = DefaultAzureCredential()
    else:
        chosen = credential
    return AzureSearchRestBackend(
        endpoint=endpoint, index_name=index_name, credential=chosen, semantic=semantic
    )


async def load_corpus(backend: AzureSearchRestBackend, dataset_dir: Path) -> dict[str, object]:
    """Create the index if needed and upload every evaluation chunk."""

    await backend.create_index()
    items = load_evaluation_corpus(dataset_dir)
    uploaded = await backend.upload([evidence_to_document(item) for item in items])
    return {"action": "load", "uploaded": uploaded}


async def check_isolation(
    backend: IndexBackend, *, settle_seconds: float = _SETTLE_SECONDS
) -> dict[str, object]:
    """Upload canaries, wait until they are searchable, then verify every caller's view."""

    await backend.create_index()
    await backend.upload([evidence_to_document(item) for item in CANARY_ITEMS])
    provider = AzureSearchRetrievalProvider(
        backend, score_mapper=saturating_score(1.0), clock=lambda: datetime.now(UTC)
    )
    widest = _CASES[2]
    for _ in range(_SETTLE_ATTEMPTS):
        try:
            if len(await _visible(provider, widest)) == len(widest.expected):
                break
        except HarnessError:
            break
        await asyncio.sleep(settle_seconds)
    outcomes: list[dict[str, Any]] = []
    for case in _CASES:
        try:
            actual = await _visible(provider, case)
            error: str | None = None
        except HarnessError as failure:
            actual, error = [], type(failure).__name__
        outcomes.append(
            {
                "case": case.name,
                "expected": sorted(case.expected),
                "actual": sorted(actual),
                "error": error,
                "passed": error is None and frozenset(actual) == case.expected,
            }
        )
    return {
        "action": "isolation",
        "cases": outcomes,
        "passed": all(outcome["passed"] for outcome in outcomes),
    }


async def _visible(provider: AzureSearchRetrievalProvider, case: _Case) -> list[str]:
    found: Sequence[RetrievedItem] = await provider.retrieve(
        identity=IdentityScope(case.tenant, "subject-canary-01"),
        authorization_labels=case.labels,
        query=_CANARY_QUERY,
        limit=20,
    )
    return [item.item_id for item in found]
