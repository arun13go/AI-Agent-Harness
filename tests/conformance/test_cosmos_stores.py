"""Check the Cosmos store details the shared suite cannot see: document shape, ttl, racing."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_harness.adapters.storage.cosmos_stores import (
    CosmosArtifactStore,
    CosmosAuditSink,
    CosmosIdempotencyLedger,
    CosmosRunStateStore,
)
from agent_harness.core.types import ArtifactRecord
from tests.conformance.fake_documents import FakeDocumentBackend
from tests.conformance.test_state_stores import (
    EVENT_SCHEMA,
    LATER,
    NOW,
    OUTSIDER,
    OWNER,
    claim,
    context,
    make_event,
    state,
)


@pytest.mark.asyncio
async def test_ids_are_opaque_and_only_expiring_records_carry_a_ttl() -> None:
    backend = FakeDocumentBackend()
    clock = lambda: NOW  # noqa: E731
    artifacts = CosmosArtifactStore(backend, clock)
    run_states = CosmosRunStateStore(backend, clock)
    ledger = CosmosIdempotencyLedger(backend, clock)

    await artifacts.save(ArtifactRecord("artifact-secret-id", OWNER, {"v": 1}, NOW, LATER))
    await run_states.save(state(OWNER), expected_version=None)
    await ledger.claim(claim())

    documents = await backend.find(OWNER.tenant_scope)
    assert len(documents) == 3
    for document in documents:
        assert document["tenantScope"] == OWNER.tenant_scope
        assert len(document["id"]) == 64 and document["id"].isalnum()
        assert (
            "artifact-secret-id" not in document["id"] and OWNER.subject_scope not in document["id"]
        )
    by_kind = {d["kind"]: d for d in documents}
    assert by_kind["artifact"]["ttl"] == 8 * 24 * 3600
    assert "ttl" not in by_kind["run-state"] and "ttl" not in by_kind["idempotency"]
    assert await backend.find(OUTSIDER.tenant_scope) == []


@pytest.mark.asyncio
async def test_an_already_expired_record_still_gets_a_positive_ttl() -> None:
    backend = FakeDocumentBackend()
    late = lambda: LATER.replace(year=LATER.year + 1)  # noqa: E731
    await CosmosArtifactStore(backend, late).save(ArtifactRecord("a", OWNER, {}, NOW, LATER))

    (document,) = await backend.find(OWNER.tenant_scope)
    assert document["ttl"] == 1


@pytest.mark.asyncio
async def test_two_writers_racing_for_one_runs_next_sequence_both_succeed() -> None:
    backend = FakeDocumentBackend(unique_field="eventId")
    first, second = CosmosAuditSink(backend, EVENT_SCHEMA), CosmosAuditSink(backend, EVENT_SCHEMA)
    run = context(OWNER)

    await asyncio.gather(
        first.append_next(make_event(event_id="event-000001", context=run)),
        second.append_next(make_event(event_id="event-000002", context=run)),
    )

    documents = await backend.find(OWNER.tenant_scope, kind="audit", runId=run.run_id)
    assert sorted(d["payload"]["sequence"] for d in documents) == [0, 1]


@pytest.mark.asyncio
async def test_audit_documents_hold_the_event_without_business_payload_fields() -> None:
    backend = FakeDocumentBackend(unique_field="eventId")
    sink = CosmosAuditSink(backend, EVENT_SCHEMA)
    await sink.append(make_event(0, attributes={"count": 3}))

    (document,) = await backend.find(OWNER.tenant_scope, kind="audit")
    assert document["payload"]["attributes"] == {"count": 3}
    assert set(document) == {
        "id",
        "tenantScope",
        "kind",
        "runId",
        "eventId",
        "subjectScope",
        "correlationId",
        "traceId",
        "payload",
        "_etag",
    }
    json.dumps(document)
