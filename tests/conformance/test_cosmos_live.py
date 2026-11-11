"""Live Cosmos DB checks that need real RBAC; skipped unless HARNESS_COSMOS_ENDPOINT is set."""

from __future__ import annotations

import uuid

import pytest

from agent_harness.core.errors import StorageError
from tests.conformance.test_state_stores import LIVE_ENDPOINT, live_backend

pytestmark = pytest.mark.skipif(not LIVE_ENDPOINT, reason="HARNESS_COSMOS_ENDPOINT is not set")


@pytest.mark.asyncio
async def test_the_audit_role_can_create_and_read_but_not_rewrite_history() -> None:
    audit = live_backend("audit")
    document_id = uuid.uuid4().hex * 2
    document = {"id": document_id, "kind": "audit", "runId": "run", "eventId": uuid.uuid4().hex}
    await audit.create("tenant-live", document)

    stored = await audit.read("tenant-live", document_id)
    assert stored is not None
    assert [d["id"] for d in await audit.find("tenant-live", kind="audit")] == [document_id]
    with pytest.raises(StorageError, match="HTTP 403"):
        await audit.replace("tenant-live", {**stored, "runId": "tampered"}, etag=stored["_etag"])


@pytest.mark.asyncio
async def test_the_records_role_can_create_read_and_replace() -> None:
    records = live_backend("records")
    document_id = uuid.uuid4().hex * 2
    await records.create("tenant-live", {"id": document_id, "kind": "probe"})

    stored = await records.read("tenant-live", document_id)
    assert stored is not None
    await records.replace("tenant-live", {**stored, "kind": "changed"}, etag=stored["_etag"])
