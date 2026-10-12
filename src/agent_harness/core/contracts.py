"""Define adapter protocols implemented by runtime and storage modules."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol

from agent_harness.core.types import (
    ActionProposal,
    ApprovalRequest,
    ApprovalResult,
    ArtifactRecord,
    Checkpoint,
    IdempotencyRecord,
    IdentityScope,
    MemoryRecord,
    Message,
    ModelRequest,
    ModelResponse,
    PendingAction,
    RequestContext,
    RetrievedItem,
    RunEvent,
    RunState,
    SourceReference,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class ModelClient(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelResponse]: ...


class RetrievalProvider(Protocol):
    async def retrieve(
        self,
        *,
        identity: IdentityScope,
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]: ...


class Chunker(Protocol):
    def chunk(
        self,
        *,
        source: SourceReference,
        content: str,
    ) -> Sequence[RetrievedItem]: ...


class ContextPolicy(Protocol):
    def build_messages(
        self,
        *,
        instructions: Sequence[Message],
        history: Sequence[Message],
        evidence: Sequence[RetrievedItem],
        maximum_tokens: int,
    ) -> Sequence[Message]: ...


class MemoryStore(Protocol):
    async def write(self, record: MemoryRecord) -> None: ...

    async def query(
        self,
        *,
        identity: IdentityScope,
        kind: str,
        limit: int,
    ) -> Sequence[MemoryRecord]: ...

    async def delete(self, *, identity: IdentityScope, record_id: str) -> None: ...


class RunStateStore(Protocol):
    async def load(self, *, identity: IdentityScope, run_id: str) -> RunState | None: ...

    async def save(self, state: RunState, *, expected_version: str | None) -> RunState: ...


class ArtifactStore(Protocol):
    async def save(self, artifact: ArtifactRecord) -> None: ...

    async def load(self, *, identity: IdentityScope, artifact_id: str) -> ArtifactRecord | None: ...


class ProposalStore(Protocol):
    async def save(self, proposal: ActionProposal) -> None: ...

    async def load(self, *, identity: IdentityScope, proposal_id: str) -> ActionProposal | None: ...


class CheckpointStore(Protocol):
    async def save(self, checkpoint: Checkpoint) -> None: ...

    async def load(self, *, identity: IdentityScope, checkpoint_id: str) -> Checkpoint | None: ...


class ApprovalProvider(Protocol):
    async def request(self, approval: ApprovalRequest) -> None: ...

    async def get_request(
        self, *, tenant_scope: str, approval_id: str
    ) -> ApprovalRequest | None: ...

    async def get(self, *, identity: IdentityScope, approval_id: str) -> ApprovalResult | None: ...

    async def decide(self, result: ApprovalResult) -> None: ...


class IdempotencyLedger(Protocol):
    async def load(self, *, identity: IdentityScope, key: str) -> IdempotencyRecord | None: ...

    async def claim(self, record: IdempotencyRecord) -> bool: ...

    async def complete(self, record: IdempotencyRecord) -> None: ...


class ActionProposer(Protocol):
    async def propose(
        self,
        *,
        authenticated_identity: IdentityScope,
        context: RequestContext,
        call: ToolCall,
    ) -> PendingAction: ...


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def invoke(
        self,
        *,
        context: RequestContext,
        call: ToolCall,
        idempotency_key: str | None,
    ) -> ToolResult: ...


class AuditSink(Protocol):
    async def append_next(self, event: RunEvent) -> None: ...

    async def append(self, event: RunEvent) -> None: ...


class TelemetryExporter(Protocol):
    async def export(
        self,
        *,
        event: RunEvent,
        attributes: Mapping[str, str | int | float | bool],
    ) -> None: ...
