"""Provide concurrency-safe in-memory stores for the action lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from agent_harness.core.errors import (
    ActionLifecycleError,
    ApprovalError,
    ConcurrencyError,
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
    RunState,
)

type ScopedKey = tuple[str, str, str]


def _key(identity: IdentityScope, record_id: str) -> ScopedKey:
    return identity.tenant_scope, identity.subject_scope, record_id


class InMemoryArtifactStore:
    """Store proposal payloads under exact identity scope."""

    def __init__(self) -> None:
        self._records: dict[ScopedKey, ArtifactRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, artifact: ArtifactRecord) -> None:
        """Save one immutable artifact."""

        key = _key(artifact.identity, artifact.artifact_id)
        async with self._lock:
            if key in self._records:
                raise ActionLifecycleError(f"Artifact '{artifact.artifact_id}' already exists")
            self._records[key] = artifact

    async def load(self, *, identity: IdentityScope, artifact_id: str) -> ArtifactRecord | None:
        """Load an artifact only from its original identity scope."""

        return self._records.get(_key(identity, artifact_id))


class InMemoryProposalStore:
    """Store immutable action proposals under exact identity scope."""

    def __init__(self) -> None:
        self._records: dict[ScopedKey, ActionProposal] = {}
        self._lock = asyncio.Lock()

    async def save(self, proposal: ActionProposal) -> None:
        """Save one immutable proposal."""

        key = _key(proposal.context.identity, proposal.proposal_id)
        async with self._lock:
            if key in self._records:
                raise ActionLifecycleError(f"Proposal '{proposal.proposal_id}' already exists")
            self._records[key] = proposal

    async def load(self, *, identity: IdentityScope, proposal_id: str) -> ActionProposal | None:
        """Load a proposal only from its original identity scope."""

        return self._records.get(_key(identity, proposal_id))


class InMemoryCheckpointStore:
    """Store immutable action resume checkpoints."""

    def __init__(self) -> None:
        self._records: dict[ScopedKey, Checkpoint] = {}
        self._lock = asyncio.Lock()

    async def save(self, checkpoint: Checkpoint) -> None:
        """Save one immutable checkpoint."""

        key = _key(checkpoint.context.identity, checkpoint.checkpoint_id)
        async with self._lock:
            if key in self._records:
                raise ActionLifecycleError(
                    f"Checkpoint '{checkpoint.checkpoint_id}' already exists"
                )
            self._records[key] = checkpoint

    async def load(self, *, identity: IdentityScope, checkpoint_id: str) -> Checkpoint | None:
        """Load a checkpoint only from its original identity scope."""

        return self._records.get(_key(identity, checkpoint_id))


class InMemoryRunStateStore:
    """Store versioned run state with optimistic concurrency."""

    def __init__(self) -> None:
        self._records: dict[ScopedKey, RunState] = {}
        self._lock = asyncio.Lock()

    async def load(self, *, identity: IdentityScope, run_id: str) -> RunState | None:
        """Load run state from its exact identity scope."""

        return self._records.get(_key(identity, run_id))

    async def save(self, state: RunState, *, expected_version: str | None) -> RunState:
        """Save run state only when its expected version matches."""

        key = _key(state.context.identity, state.context.run_id)
        async with self._lock:
            existing = self._records.get(key)
            if existing is None:
                if expected_version is not None:
                    raise ConcurrencyError("Run state does not exist at the expected version")
                version = "1"
            else:
                if existing.version != expected_version:
                    raise ConcurrencyError(
                        f"Run state expected version '{expected_version}', "
                        f"found '{existing.version}'"
                    )
                version = str(int(existing.version) + 1)
            stored = replace(state, version=version)
            self._records[key] = stored
            return stored


class InMemoryApprovalProvider:
    """Store approval requests and one immutable decision."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._results: dict[str, ApprovalResult] = {}
        self._lock = asyncio.Lock()

    async def request(self, approval: ApprovalRequest) -> None:
        """Create one approval request."""

        async with self._lock:
            if approval.approval_id in self._requests:
                raise ApprovalError(f"Approval '{approval.approval_id}' already exists")
            self._requests[approval.approval_id] = approval

    async def get_request(self, *, tenant_scope: str, approval_id: str) -> ApprovalRequest | None:
        """Load a request only from its original tenant."""

        request = self._requests.get(approval_id)
        if request is None or request.context.identity.tenant_scope != tenant_scope:
            return None
        return request

    async def get(self, *, identity: IdentityScope, approval_id: str) -> ApprovalResult | None:
        """Load a decision only for its proposal identity."""

        request = await self.get_request(
            tenant_scope=identity.tenant_scope,
            approval_id=approval_id,
        )
        if request is None:
            return None
        if request.context.identity != identity:
            return None
        return self._results.get(approval_id)

    async def decide(self, result: ApprovalResult) -> None:
        """Record one tenant-matched decision bound to the request."""

        async with self._lock:
            request = self._requests.get(result.approval_id)
            if request is None:
                raise ApprovalError(f"Approval '{result.approval_id}' does not exist")
            if request.context.identity.tenant_scope != result.decided_by.tenant_scope:
                raise ApprovalError("Approver tenant does not match the proposal tenant")
            if (
                request.proposal_ref != result.proposal_ref
                or request.proposal_digest != result.proposal_digest
            ):
                raise ApprovalError("Approval decision does not match the proposal")
            if result.approval_id in self._results:
                raise ApprovalError(f"Approval '{result.approval_id}' already has a decision")
            self._results[result.approval_id] = result


class InMemoryIdempotencyLedger:
    """Atomically claim and complete one action effect."""

    def __init__(self) -> None:
        self._records: dict[ScopedKey, IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    async def load(self, *, identity: IdentityScope, key: str) -> IdempotencyRecord | None:
        """Load an execution record from its exact identity scope."""

        return self._records.get(_key(identity, key))

    async def claim(self, record: IdempotencyRecord) -> bool:
        """Claim an unstarted action exactly once."""

        if record.status is not IdempotencyStatus.IN_PROGRESS:
            raise ActionLifecycleError("Idempotency claims must start in progress")
        key = _key(record.identity, record.key)
        async with self._lock:
            if key in self._records:
                return False
            self._records[key] = record
            return True

    async def complete(self, record: IdempotencyRecord) -> None:
        """Replace an in-progress claim with its terminal outcome."""

        key = _key(record.identity, record.key)
        async with self._lock:
            existing = self._records.get(key)
            if existing is None or existing.status is not IdempotencyStatus.IN_PROGRESS:
                raise ConcurrencyError("Action does not have an in-progress idempotency claim")
            if existing.proposal_ref != record.proposal_ref:
                raise ConcurrencyError("Idempotency proposal binding cannot change")
            if record.status in (IdempotencyStatus.NOT_STARTED, IdempotencyStatus.IN_PROGRESS):
                raise ActionLifecycleError("Idempotency completion requires a terminal status")
            self._records[key] = record
