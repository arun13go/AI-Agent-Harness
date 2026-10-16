"""Verify concurrent event writers receive unique ordered sequences."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.core.event_writer import RunEventWriter
from agent_harness.core.types import IdentityScope, RequestContext

ROOT = Path(__file__).parents[2]
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"


@pytest.mark.asyncio
async def test_concurrent_writers_allocate_unique_sequences() -> None:
    context = RequestContext(
        identity=IdentityScope("tenant-0001", "subject-0001"),
        run_id="run-00000001",
        correlation_id="correlation-0001",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    sink = InMemoryAuditSink(EVENT_SCHEMA)
    writers = [
        RunEventWriter(context=context, audit_sink=sink),
        RunEventWriter(context=context, audit_sink=sink),
    ]

    await asyncio.gather(
        *[
            writer.emit("run.started", source="app", outcome="started", attributes={})
            for writer in writers
        ]
    )

    assert [event.sequence for event in sink.events] == [0, 1]
