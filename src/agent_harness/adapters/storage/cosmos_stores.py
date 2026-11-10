"""Build the durable state stores on a four-method document backend (ADR 0032).

Cosmos DB is the reference backend, partitioned by tenant. Every document id is a hash of the
record kind, subject and id, so a read with the wrong identity finds nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from weakref import WeakValueDictionary

from pydantic import TypeAdapter

from agent_harness.adapters.storage.in_memory_audit import load_event_validator, validate_event
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
    ApprovalRequest,
    ApprovalResult,
    ArtifactRecord,
    Checkpoint,
    IdempotencyRecord,
    IdempotencyStatus,
    IdentityScope,
    RunEvent,
    RunState,
)

type Document = dict[str, Any]

_EXPIRY_GRACE = timedelta(days=7)


class DocumentConflict(Exception):
    """A create found its id or unique key taken, or a replace saw a changed ETag."""


class DocumentBackend(Protocol):
    """Tenant-partitioned documents; ``read`` results carry the current ``_etag``."""

    async def create(self, partition: str, document: Document) -> None: ...

    async def read(self, partition: str, document_id: str) -> Document | None: ...

    async def replace(self, partition: str, document: Document, *, etag: str) -> None: ...

    async def find(self, partition: str, **equals: str) -> list[Document]: ...


def _doc_id(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


class _Collection[T]:
    """Store one record kind as JSON documents with optional expiry."""

    def __init__(
        self,
        backend: DocumentBackend,
        kind: str,
        record_type: type[T],
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._backend = backend
        self._kind = kind
        self._codec = TypeAdapter(record_type)
        self._clock = clock or (lambda: datetime.now(UTC))

    def _id(self, subject: str | None, record_id: str) -> str:
        return _doc_id(self._kind, subject or "", record_id)

    def _document(
        self,
        tenant: str,
        subject: str | None,
        record_id: str,
        record: T,
        expires_at: datetime | None,
    ) -> Document:
        document: Document = {
            "id": self._id(subject, record_id),
            "tenantScope": tenant,
            "kind": self._kind,
            "payload": self._codec.dump_python(record, mode="json"),
        }
        if expires_at is not None:
            remaining = expires_at + _EXPIRY_GRACE - self._clock()
            document["ttl"] = max(1, int(remaining.total_seconds()))
        return document

    async def create(
        self,
        tenant: str,
        subject: str | None,
        record_id: str,
        record: T,
        expires_at: datetime | None = None,
    ) -> bool:
        """Create the record; return False when it already exists."""

        try:
            await self._backend.create(
                tenant, self._document(tenant, subject, record_id, record, expires_at)
            )
        except DocumentConflict:
            return False
        return True

    async def read(self, tenant: str, subject: str | None, record_id: str) -> tuple[T, str] | None:
        """Return the record and its ETag, or None."""

        document = await self._backend.read(tenant, self._id(subject, record_id))
        if document is None:
            return None
        return self._codec.validate_python(document["payload"]), document["_etag"]

    async def replace(
        self,
        tenant: str,
        subject: str | None,
        record_id: str,
        record: T,
        etag: str,
        expires_at: datetime | None = None,
    ) -> bool:
        """Replace the record if its ETag still matches; return False when it changed."""

        try:
            await self._backend.replace(
                tenant,
                self._document(tenant, subject, record_id, record, expires_at),
                etag=etag,
            )
        except DocumentConflict:
            return False
        return True


class _ImmutableStore[T]:
    def __init__(
        self,
        backend: DocumentBackend,
        kind: str,
        record_type: type[T],
        clock: Callable[[], datetime] | None,
    ) -> None:
        self._records = _Collection(backend, kind, record_type, clock)

    async def _save(
        self, identity: IdentityScope, record_id: str, record: T, expires_at: datetime, label: str
    ) -> None:
        if not await self._records.create(
            identity.tenant_scope, identity.subject_scope, record_id, record, expires_at
        ):
            raise ActionLifecycleError(f"{label} '{record_id}' already exists")

    async def _load(self, identity: IdentityScope, record_id: str) -> T | None:
        found = await self._records.read(identity.tenant_scope, identity.subject_scope, record_id)
        return None if found is None else found[0]


class CosmosArtifactStore(_ImmutableStore[ArtifactRecord]):
    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        super().__init__(backend, "artifact", ArtifactRecord, clock)

    async def save(self, artifact: ArtifactRecord) -> None:
        await self._save(
            artifact.identity, artifact.artifact_id, artifact, artifact.expires_at, "Artifact"
        )

    async def load(self, *, identity: IdentityScope, artifact_id: str) -> ArtifactRecord | None:
        return await self._load(identity, artifact_id)


class CosmosProposalStore(_ImmutableStore[ActionProposal]):
    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        super().__init__(backend, "proposal", ActionProposal, clock)

    async def save(self, proposal: ActionProposal) -> None:
        await self._save(
            proposal.context.identity,
            proposal.proposal_id,
            proposal,
            proposal.expires_at,
            "Proposal",
        )

    async def load(self, *, identity: IdentityScope, proposal_id: str) -> ActionProposal | None:
        return await self._load(identity, proposal_id)


class CosmosCheckpointStore(_ImmutableStore[Checkpoint]):
    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        super().__init__(backend, "checkpoint", Checkpoint, clock)

    async def save(self, checkpoint: Checkpoint) -> None:
        await self._save(
            checkpoint.context.identity,
            checkpoint.checkpoint_id,
            checkpoint,
            checkpoint.expires_at,
            "Checkpoint",
        )

    async def load(self, *, identity: IdentityScope, checkpoint_id: str) -> Checkpoint | None:
        return await self._load(identity, checkpoint_id)


class CosmosRunStateStore:
    """Versioned run state; the document ETag makes the version check atomic."""

    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        self._records = _Collection(backend, "run-state", RunState, clock)

    async def load(self, *, identity: IdentityScope, run_id: str) -> RunState | None:
        found = await self._records.read(identity.tenant_scope, identity.subject_scope, run_id)
        return None if found is None else found[0]

    async def save(self, state: RunState, *, expected_version: str | None) -> RunState:
        identity, run_id = state.context.identity, state.context.run_id
        tenant, subject = identity.tenant_scope, identity.subject_scope
        if expected_version is None:
            stored = replace(state, version="1")
            if not await self._records.create(tenant, subject, run_id, stored):
                raise ConcurrencyError("Run state already exists")
            return stored
        found = await self._records.read(tenant, subject, run_id)
        if found is None:
            raise ConcurrencyError("Run state does not exist at the expected version")
        existing, etag = found
        if existing.version != expected_version:
            raise ConcurrencyError(
                f"Run state expected version '{expected_version}', found '{existing.version}'"
            )
        stored = replace(state, version=str(int(existing.version) + 1))
        if not await self._records.replace(tenant, subject, run_id, stored, etag):
            raise ConcurrencyError("Run state changed concurrently")
        return stored


class CosmosIdempotencyLedger:
    """Claim an action effect once; completion replaces the claim under its ETag."""

    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        self._records = _Collection(backend, "idempotency", IdempotencyRecord, clock)

    async def load(self, *, identity: IdentityScope, key: str) -> IdempotencyRecord | None:
        found = await self._records.read(identity.tenant_scope, identity.subject_scope, key)
        return None if found is None else found[0]

    async def claim(self, record: IdempotencyRecord) -> bool:
        if record.status is not IdempotencyStatus.IN_PROGRESS:
            raise ActionLifecycleError("Idempotency claims must start in progress")
        identity = record.identity
        return await self._records.create(
            identity.tenant_scope, identity.subject_scope, record.key, record
        )

    async def complete(self, record: IdempotencyRecord) -> None:
        identity = record.identity
        tenant, subject = identity.tenant_scope, identity.subject_scope
        found = await self._records.read(tenant, subject, record.key)
        if found is None or found[0].status is not IdempotencyStatus.IN_PROGRESS:
            raise ConcurrencyError("Action does not have an in-progress idempotency claim")
        existing, etag = found
        if existing.proposal_ref != record.proposal_ref:
            raise ConcurrencyError("Idempotency proposal binding cannot change")
        if record.status in (IdempotencyStatus.NOT_STARTED, IdempotencyStatus.IN_PROGRESS):
            raise ActionLifecycleError("Idempotency completion requires a terminal status")
        if not await self._records.replace(tenant, subject, record.key, record, etag):
            raise ConcurrencyError("Action does not have an in-progress idempotency claim")


class CosmosApprovalProvider:
    """One request and one decision per approval, keyed by tenant and approval id."""

    def __init__(self, backend: DocumentBackend, clock: Callable[[], datetime] | None = None):
        self._requests = _Collection(backend, "approval", ApprovalRequest, clock)
        self._results = _Collection(backend, "approval-result", ApprovalResult, clock)

    async def request(self, approval: ApprovalRequest) -> None:
        tenant = approval.context.identity.tenant_scope
        if not await self._requests.create(
            tenant, None, approval.approval_id, approval, approval.expires_at
        ):
            raise ApprovalError(f"Approval '{approval.approval_id}' already exists")

    async def get_request(self, *, tenant_scope: str, approval_id: str) -> ApprovalRequest | None:
        found = await self._requests.read(tenant_scope, None, approval_id)
        return None if found is None else found[0]

    async def get(self, *, identity: IdentityScope, approval_id: str) -> ApprovalResult | None:
        request = await self.get_request(
            tenant_scope=identity.tenant_scope, approval_id=approval_id
        )
        if request is None or request.context.identity != identity:
            return None
        found = await self._results.read(identity.tenant_scope, None, approval_id)
        return None if found is None else found[0]

    async def decide(self, result: ApprovalResult) -> None:
        tenant = result.decided_by.tenant_scope
        request = await self.get_request(tenant_scope=tenant, approval_id=result.approval_id)
        if request is None:
            raise ApprovalError(f"Approval '{result.approval_id}' does not exist")
        if (
            request.proposal_ref != result.proposal_ref
            or request.proposal_digest != result.proposal_digest
        ):
            raise ApprovalError("Approval decision does not match the proposal")
        if not await self._results.create(
            tenant, None, result.approval_id, result, request.expires_at
        ):
            raise ApprovalError(f"Approval '{result.approval_id}' already has a decision")


class CosmosAuditSink:
    """Append-only run events: one document per run sequence, one unique event id.

    The backend container must enforce a unique key on ``eventId`` within a partition.
    """

    def __init__(self, backend: DocumentBackend, schema_path: Path) -> None:
        self._backend = backend
        self._validator = load_event_validator(schema_path)
        self._codec = TypeAdapter(RunEvent)
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()

    async def append(self, event: RunEvent) -> None:
        validate_event(self._validator, event)
        context = event.context
        tenant, run = context.identity.tenant_scope, context.run_id
        if event.sequence > 0:
            previous = await self._backend.read(
                tenant, _doc_id("audit", run, str(event.sequence - 1))
            )
            if previous is None:
                raise EventSequenceError(
                    f"Run '{run}' has no event before sequence {event.sequence}"
                )
            if (
                previous["subjectScope"] != context.identity.subject_scope
                or previous["correlationId"] != context.correlation_id
                or previous["traceId"] != context.trace_id
            ):
                raise AuditError(f"Run '{run}' cannot change identity or correlation")
        document_id = _doc_id("audit", run, str(event.sequence))
        document: Document = {
            "id": document_id,
            "tenantScope": tenant,
            "kind": "audit",
            "runId": run,
            "eventId": event.event_id,
            "subjectScope": context.identity.subject_scope,
            "correlationId": context.correlation_id,
            "traceId": context.trace_id,
            "payload": self._codec.dump_python(event, mode="json"),
        }
        try:
            await self._backend.create(tenant, document)
        except DocumentConflict:
            taken = await self._backend.read(tenant, document_id)
            if taken is not None and taken["eventId"] != event.event_id:
                raise EventSequenceError(
                    f"Run '{run}' already has an event at sequence {event.sequence}"
                ) from None
            raise DuplicateEventError(f"Event ID '{event.event_id}' already exists") from None

    async def append_next(self, event: RunEvent) -> None:
        """Append at the run's next sequence, retrying once if another writer got there first."""

        run_key = (event.context.identity.tenant_scope, event.context.run_id)
        lock = self._locks.get(run_key) or self._locks.setdefault(run_key, asyncio.Lock())
        async with lock:
            for attempt in (1, 2):
                recorded = await self._backend.find(
                    run_key[0], kind="audit", runId=event.context.run_id
                )
                try:
                    await self.append(replace(event, sequence=len(recorded)))
                except EventSequenceError:
                    if attempt == 2:
                        raise
                else:
                    return
