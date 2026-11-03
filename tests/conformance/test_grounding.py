"""Prove grounded evidence remains scoped, attributable, fresh, and safely routed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.core.answerability import DeterministicAnswerabilityPolicy
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import (
    AnswerabilityRoute,
    EvidenceFlag,
    IdentityScope,
    RetrievedItem,
    SourceReference,
)

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
IDENTITY = IdentityScope("tenant-0001", "subject-0001")


def make_item(
    item_id: str,
    *,
    tenant_scope: str = "tenant-0001",
    authorization_labels: tuple[str, ...] = ("group:readers",),
    score: float = 0.9,
    fresh_until: datetime | None = None,
    flags: tuple[EvidenceFlag, ...] = (),
    source_label: str = "untrusted-retrieved-content",
) -> RetrievedItem:
    return RetrievedItem(
        item_id=item_id,
        content=f"Evidence for {item_id}",
        score=score,
        source=SourceReference(
            source_id=f"source-{item_id}",
            source_version="v1",
            structural_path=f"/items/{item_id}",
            ingested_at=NOW - timedelta(days=1),
            fresh_until=fresh_until or NOW + timedelta(days=1),
        ),
        tenant_scope=tenant_scope,
        authorization_labels=authorization_labels,
        evidence_flags=flags,
        source_label=source_label,
    )


@pytest.mark.asyncio
async def test_retrieval_filters_tenant_and_authorization_before_ranking() -> None:
    provider = InMemoryRetrievalProvider(
        [
            make_item("allowed-high", score=0.95),
            make_item("allowed-low", score=0.6),
            make_item("public", authorization_labels=(), score=0.8),
            make_item("false-trusted", score=0.7, source_label="trusted-instruction"),
            make_item("wrong-group", authorization_labels=("group:admins",), score=1.0),
            make_item("wrong-tenant", tenant_scope="tenant-0002", score=1.0),
        ],
        clock=lambda: NOW,
    )

    results = await provider.retrieve(
        identity=IDENTITY,
        authorization_labels=("group:readers",),
        query="status",
        limit=4,
    )

    assert [item.item_id for item in results] == [
        "allowed-high",
        "public",
        "false-trusted",
        "allowed-low",
    ]
    assert all(item.retrieved_at == NOW for item in results)
    assert all(item.source_label == "untrusted-retrieved-content" for item in results)


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "limit"), [("", 1), ("status", 0), ("status", 101)])
async def test_retrieval_rejects_invalid_query_or_limit(query: str, limit: int) -> None:
    provider = InMemoryRetrievalProvider([], clock=lambda: NOW)

    with pytest.raises(InputValidationError):
        await provider.retrieve(
            identity=IDENTITY,
            authorization_labels=(),
            query=query,
            limit=limit,
        )


@pytest.mark.parametrize(
    ("evidence", "attempt", "configured_route", "expected_route"),
    [
        ([make_item("sufficient")], 0, AnswerabilityRoute.CLARIFY, AnswerabilityRoute.ANSWER),
        ([], 0, AnswerabilityRoute.CLARIFY, AnswerabilityRoute.RETRIEVE_AGAIN),
        ([], 1, AnswerabilityRoute.CLARIFY, AnswerabilityRoute.CLARIFY),
        (
            [make_item("weak", score=0.2)],
            0,
            AnswerabilityRoute.ABSTAIN,
            AnswerabilityRoute.RETRIEVE_AGAIN,
        ),
        (
            [make_item("stale", fresh_until=NOW - timedelta(seconds=1))],
            1,
            AnswerabilityRoute.ABSTAIN,
            AnswerabilityRoute.ABSTAIN,
        ),
        (
            [make_item("conflicting", flags=(EvidenceFlag.CONFLICTING,))],
            1,
            AnswerabilityRoute.ABSTAIN,
            AnswerabilityRoute.CLARIFY,
        ),
        (
            [make_item("malicious", flags=(EvidenceFlag.MALICIOUS,))],
            0,
            AnswerabilityRoute.CLARIFY,
            AnswerabilityRoute.ABSTAIN,
        ),
    ],
)
def test_answerability_routes_evidence_safely(
    evidence: list[RetrievedItem],
    attempt: int,
    configured_route: AnswerabilityRoute,
    expected_route: AnswerabilityRoute,
) -> None:
    policy = DeterministicAnswerabilityPolicy(
        minimum_score=0.5,
        insufficient_evidence_route=configured_route,
        maximum_retrieval_attempts=1,
    )

    decision = policy.assess(evidence=evidence, now=NOW, retrieval_attempt=attempt)

    assert decision.route is expected_route
    if expected_route is AnswerabilityRoute.ANSWER:
        assert decision.citations[0].source_id == "source-sufficient"
        assert decision.citations[0].source_version == "v1"
    else:
        assert decision.citations == ()
