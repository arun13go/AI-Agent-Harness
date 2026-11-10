"""Shared behavior every state-store adapter must show: isolation, concurrency, replay, order.

Add an adapter by adding a param to ``stores`` and ``audit``. Expiry and deletion apply to memory,
which has no store yet (ADR 0029), so they are not covered here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_harness.adapters.storage.cosmos_stores import (
    CosmosApprovalProvider,
    CosmosArtifactStore,
    CosmosAuditSink,
    CosmosCheckpointStore,
    CosmosIdempotencyLedger,
    CosmosProposalStore,
    CosmosRunStateStore,
)
from agent_harness.adapters.storage.in_memory_action_stores import (
    InMemoryApprovalProvider,
    InMemoryArtifactStore,
    InMemoryCheckpointStore,
    InMemoryIdempotencyLedger,
    InMemoryProposalStore,
    InMemoryRunStateStore,
)
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.core.contracts import (
    ApprovalProvider,
    ArtifactStore,
    AuditSink,
    CheckpointStore,
    IdempotencyLedger,
    ProposalStore,
    RunStateStore,
)
from agent_harness.core.errors import (
    ActionLifecycleError,
    ApprovalError,
    AuditError,
    ConcurrencyError,
    DuplicateEventError,
    EventSequenceError,
)
from agent_harness.core.types import (
    ActionProposal,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ArtifactRecord,
    Checkpoint,
    IdempotencyRecord,
    IdempotencyStatus,
    IdentityScope,
    RequestContext,
    RunState,
    RunStatus,
    ToolResult,
)
from tests.conformance.fake_documents import FakeDocumentBackend
from tests.core.test_governance import make_context, make_event

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)
OWNER = IdentityScope("tenant-0001", "subject-0001")
COLLEAGUE = IdentityScope("tenant-0001", "subject-0002")
OUTSIDER = IdentityScope("tenant-0002", "subject-0001")
EVENT_SCHEMA = Path(__file__).parents[2] / "config" / "schema" / "v1" / "run-event.schema.json"


@dataclass(frozen=True)
class Stores:
    artifact: ArtifactStore
    proposal: ProposalStore
    checkpoint: CheckpointStore
    run_state: RunStateStore
    approval: ApprovalProvider
    idempotency: IdempotencyLedger


@pytest.fixture(params=["in-memory", "cosmos-fake"])
def stores(request: pytest.FixtureRequest) -> Stores:
    if request.param == "in-memory":
        return Stores(
            InMemoryArtifactStore(),
            InMemoryProposalStore(),
            InMemoryCheckpointStore(),
            InMemoryRunStateStore(),
            InMemoryApprovalProvider(),
            InMemoryIdempotencyLedger(),
        )
    backend = FakeDocumentBackend()
    return Stores(
        CosmosArtifactStore(backend),
        CosmosProposalStore(backend),
        CosmosCheckpointStore(backend),
        CosmosRunStateStore(backend),
        CosmosApprovalProvider(backend),
        CosmosIdempotencyLedger(backend),
    )


@dataclass(frozen=True)
class Audit:
    sink: AuditSink
    recorded: Callable[[], Awaitable[list[tuple[str, int, str]]]]


@pytest.fixture(params=["in-memory", "cosmos-fake"])
def audit(request: pytest.FixtureRequest) -> Audit:
    """The sink, and a reader of (run id, sequence, event id) for every event it holds."""

    if request.param == "in-memory":
        memory = InMemoryAuditSink(EVENT_SCHEMA)

        async def from_memory() -> list[tuple[str, int, str]]:
            return [(e.context.run_id, e.sequence, e.event_id) for e in memory.events]

        return Audit(memory, from_memory)
    backend = FakeDocumentBackend(unique_field="eventId")

    async def from_backend() -> list[tuple[str, int, str]]:
        documents = [
            d
            for tenant in (OWNER.tenant_scope, OUTSIDER.tenant_scope)
            for d in await backend.find(tenant, kind="audit")
        ]
        return [(d["runId"], d["payload"]["sequence"], d["eventId"]) for d in documents]

    return Audit(CosmosAuditSink(backend, EVENT_SCHEMA), from_backend)


def context(identity: IdentityScope, run_id: str = "run-00000001") -> RequestContext:
    return make_context(tenant=identity.tenant_scope, subject=identity.subject_scope, run_id=run_id)


@dataclass(frozen=True)
class Kind:
    """One immutable record kind: how to build, save and load it."""

    build: Callable[[IdentityScope, str], Any]
    save: Callable[[Stores, Any], Awaitable[None]]
    load: Callable[[Stores, IdentityScope, str], Awaitable[Any]]


IMMUTABLE = {
    "artifact": Kind(
        build=lambda i, r: ArtifactRecord(r, i, {"v": 1}, NOW, LATER),
        save=lambda s, rec: s.artifact.save(rec),
        load=lambda s, i, r: s.artifact.load(identity=i, artifact_id=r),
    ),
    "proposal": Kind(
        build=lambda i, r: ActionProposal(r, context(i), "tool", "args-ref", "digest", NOW, LATER),
        save=lambda s, rec: s.proposal.save(rec),
        load=lambda s, i, r: s.proposal.load(identity=i, proposal_id=r),
    ),
    "checkpoint": Kind(
        build=lambda i, r: Checkpoint(r, context(i), "prop", "digest", "appr", "1", NOW, LATER),
        save=lambda s, rec: s.checkpoint.save(rec),
        load=lambda s, i, r: s.checkpoint.load(identity=i, checkpoint_id=r),
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", IMMUTABLE)
async def test_immutable_records_are_exact_scope_and_cannot_be_overwritten(
    stores: Stores, kind: str
) -> None:
    kind_ = IMMUTABLE[kind]
    build, save, load = kind_.build, kind_.save, kind_.load
    record = build(OWNER, "rec-1")

    await save(stores, record)

    assert await load(stores, OWNER, "rec-1") == record
    assert await load(stores, COLLEAGUE, "rec-1") is None
    assert await load(stores, OUTSIDER, "rec-1") is None
    assert await load(stores, OWNER, "rec-2") is None
    with pytest.raises(ActionLifecycleError):
        await save(stores, build(OWNER, "rec-1"))
    other = build(COLLEAGUE, "rec-1")
    await save(stores, other)
    assert await load(stores, COLLEAGUE, "rec-1") == other
    assert await load(stores, OWNER, "rec-1") == record


def state(identity: IdentityScope, **values: Any) -> RunState:
    return RunState(context(identity), RunStatus.RUNNING, "ignored", NOW, values)


async def version_of(stores: Stores, identity: IdentityScope) -> str | None:
    loaded = await stores.run_state.load(identity=identity, run_id="run-00000001")
    return None if loaded is None else loaded.version


@pytest.mark.asyncio
async def test_run_state_versions_advance_only_from_the_expected_version(stores: Stores) -> None:
    first = await stores.run_state.save(state(OWNER, n=1), expected_version=None)
    second = await stores.run_state.save(state(OWNER, n=2), expected_version=first.version)

    assert (first.version, second.version) == ("1", "2")
    loaded = await stores.run_state.load(identity=OWNER, run_id="run-00000001")
    assert loaded == second and loaded is not None and loaded.values == {"n": 2}
    for expected in (None, "1", "3"):
        with pytest.raises(ConcurrencyError):
            await stores.run_state.save(state(OWNER, n=3), expected_version=expected)
    with pytest.raises(ConcurrencyError):
        await stores.run_state.save(
            RunState(context(OWNER, "run-00000009"), RunStatus.RUNNING, "x", NOW, {}),
            expected_version="1",
        )
    assert await version_of(stores, OWNER) == "2"


@pytest.mark.asyncio
async def test_run_state_is_isolated_and_versioned_per_identity(stores: Stores) -> None:
    await stores.run_state.save(state(OWNER, who="owner"), expected_version=None)
    other = await stores.run_state.save(state(COLLEAGUE, who="colleague"), expected_version=None)

    assert other.version == "1"
    owner = await stores.run_state.load(identity=OWNER, run_id="run-00000001")
    colleague = await stores.run_state.load(identity=COLLEAGUE, run_id="run-00000001")
    assert owner is not None and owner.values == {"who": "owner"}
    assert colleague is not None and colleague.values == {"who": "colleague"}
    assert await stores.run_state.load(identity=OUTSIDER, run_id="run-00000001") is None


@pytest.mark.asyncio
async def test_exactly_one_of_many_concurrent_writers_wins(stores: Stores) -> None:
    base = await stores.run_state.save(state(OWNER), expected_version=None)

    results = await asyncio.gather(
        *(
            stores.run_state.save(state(OWNER, writer=i), expected_version=base.version)
            for i in range(10)
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(r, RunState) for r in results) == 1
    assert sum(isinstance(r, ConcurrencyError) for r in results) == 9
    assert await version_of(stores, OWNER) == "2"


def claim(identity: IdentityScope = OWNER, key: str = "key-1", ref: str = "prop-1") -> Any:
    return IdempotencyRecord(key, identity, ref, IdempotencyStatus.IN_PROGRESS, None, NOW)


def done(record: Any, status: IdempotencyStatus = IdempotencyStatus.SUCCEEDED) -> Any:
    result = ToolResult("call-1", {"ok": True}) if status is IdempotencyStatus.SUCCEEDED else None
    return IdempotencyRecord(record.key, record.identity, record.proposal_ref, status, result, NOW)


@pytest.mark.asyncio
async def test_an_action_is_claimed_once_even_under_concurrent_delivery(stores: Stores) -> None:
    wins = await asyncio.gather(*(stores.idempotency.claim(claim()) for _ in range(10)))

    assert sum(wins) == 1
    assert await stores.idempotency.claim(claim(COLLEAGUE)) is True
    assert await stores.idempotency.claim(claim(OUTSIDER)) is True
    stored = await stores.idempotency.load(identity=OWNER, key="key-1")
    assert stored is not None and stored.status is IdempotencyStatus.IN_PROGRESS
    assert await stores.idempotency.load(identity=OWNER, key="other") is None


@pytest.mark.asyncio
async def test_a_claim_completes_once_and_keeps_its_proposal_binding(stores: Stores) -> None:
    with pytest.raises(ConcurrencyError):
        await stores.idempotency.complete(done(claim()))
    await stores.idempotency.claim(claim())
    with pytest.raises(ConcurrencyError):
        await stores.idempotency.complete(done(claim(ref="prop-2")))
    for status in (IdempotencyStatus.NOT_STARTED, IdempotencyStatus.IN_PROGRESS):
        with pytest.raises(ActionLifecycleError):
            await stores.idempotency.complete(done(claim(), status))

    await stores.idempotency.complete(done(claim()))

    stored = await stores.idempotency.load(identity=OWNER, key="key-1")
    assert stored is not None and stored.status is IdempotencyStatus.SUCCEEDED
    with pytest.raises(ConcurrencyError):
        await stores.idempotency.complete(done(claim()))
    assert await stores.idempotency.claim(claim()) is False
    with pytest.raises(ActionLifecycleError):
        await stores.idempotency.claim(done(claim()))


@pytest.mark.asyncio
async def test_a_claim_completes_once_even_when_completed_concurrently(stores: Stores) -> None:
    await stores.idempotency.claim(claim())

    outcomes = await asyncio.gather(
        stores.idempotency.complete(done(claim())),
        stores.idempotency.complete(done(claim())),
        return_exceptions=True,
    )

    assert sorted(type(o).__name__ for o in outcomes) == ["ConcurrencyError", "NoneType"]


def approval_request(approval_id: str = "appr-1") -> ApprovalRequest:
    return ApprovalRequest(approval_id, context(OWNER), "prop-1", "digest-1", "tool", LATER)


def decision(approver: IdentityScope = COLLEAGUE, **changes: Any) -> ApprovalResult:
    fields: dict[str, Any] = {
        "approval_id": "appr-1",
        "proposal_ref": "prop-1",
        "proposal_digest": "digest-1",
        "decision": ApprovalDecision.APPROVED,
        "decided_by": approver,
        "decided_at": NOW,
    }
    return ApprovalResult(**{**fields, **changes})


@pytest.mark.asyncio
async def test_an_approval_is_requested_once_and_decided_once_by_a_tenant_member(
    stores: Stores,
) -> None:
    await stores.approval.request(approval_request())
    with pytest.raises(ApprovalError):
        await stores.approval.request(approval_request())
    assert await stores.approval.get(identity=OWNER, approval_id="appr-1") is None

    await stores.approval.decide(decision())

    result = await stores.approval.get(identity=OWNER, approval_id="appr-1")
    assert result == decision()
    with pytest.raises(ApprovalError):
        await stores.approval.decide(decision(decision=ApprovalDecision.DENIED))
    assert await stores.approval.get(identity=OWNER, approval_id="appr-1") == decision()


@pytest.mark.asyncio
async def test_an_approval_is_visible_only_to_its_tenant_and_proposer(stores: Stores) -> None:
    request = approval_request()
    await stores.approval.request(request)
    await stores.approval.decide(decision())

    assert await stores.approval.get(identity=COLLEAGUE, approval_id="appr-1") is None
    assert await stores.approval.get(identity=OUTSIDER, approval_id="appr-1") is None
    assert (
        await stores.approval.get_request(tenant_scope="tenant-0002", approval_id="appr-1") is None
    )
    found = await stores.approval.get_request(tenant_scope="tenant-0001", approval_id="appr-1")
    assert found == request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"approver": OUTSIDER},
        {"proposal_digest": "tampered"},
        {"proposal_ref": "other-proposal"},
        {"approval_id": "missing"},
    ],
)
async def test_a_decision_that_does_not_match_its_request_is_refused(
    stores: Stores, bad: dict[str, Any]
) -> None:
    await stores.approval.request(approval_request())
    changes = dict(bad)
    approver = changes.pop("approver", COLLEAGUE)

    with pytest.raises(ApprovalError):
        await stores.approval.decide(decision(approver, **changes))

    assert await stores.approval.get(identity=OWNER, approval_id="appr-1") is None


@pytest.mark.asyncio
async def test_audit_refuses_an_event_that_breaks_the_schema_and_records_nothing(
    audit: Audit,
) -> None:
    with pytest.raises(AuditError):
        await audit.sink.append(make_event(0, attributes={"prompt": "business text"}))

    assert await audit.recorded() == []


@pytest.mark.asyncio
async def test_audit_assigns_gapless_sequences_under_concurrency(audit: Audit) -> None:
    run = context(OWNER)

    await asyncio.gather(
        *(
            audit.sink.append_next(make_event(event_id=f"event-{i:06d}", context=run))
            for i in range(20)
        )
    )

    recorded = await audit.recorded()
    assert sorted(sequence for _, sequence, _ in recorded) == list(range(20))
    assert len({event_id for _, _, event_id in recorded}) == 20


@pytest.mark.asyncio
async def test_audit_is_append_only_ordered_and_bound_to_one_run_identity(audit: Audit) -> None:
    run = context(OWNER)
    await audit.sink.append(make_event(0, event_id="event-000001", context=run))

    with pytest.raises(DuplicateEventError):
        await audit.sink.append(make_event(1, event_id="event-000001", context=run))
    with pytest.raises(DuplicateEventError):
        await audit.sink.append(make_event(0, event_id="event-000001", context=run))
    for sequence in (0, 2):
        with pytest.raises(EventSequenceError):
            await audit.sink.append(make_event(sequence, event_id="event-000002", context=run))
    with pytest.raises(AuditError):
        await audit.sink.append(make_event(1, event_id="event-000003", context=context(COLLEAGUE)))
    await audit.sink.append(make_event(1, event_id="event-000004", context=run))
    for changed in (
        replace(run, correlation_id="correlation-other"),
        replace(run, trace_id="f" * 32),
    ):
        with pytest.raises(AuditError):
            await audit.sink.append(make_event(2, event_id="event-000006", context=changed))
    other = context(OUTSIDER, "run-00000002")
    await audit.sink.append(make_event(0, event_id="event-000005", context=other))

    recorded = await audit.recorded()
    assert sorted((run_id, sequence) for run_id, sequence, _ in recorded) == [
        ("run-00000001", 0),
        ("run-00000001", 1),
        ("run-00000002", 0),
    ]
