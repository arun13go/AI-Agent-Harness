"""Define adapter protocols implemented by runtime and storage modules."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from agent_harness.core.types import (
    ActionProposal,
    AnswerabilityDecision,
    ApprovalRequest,
    ApprovalResult,
    ArtifactRecord,
    Checkpoint,
    ContextPlan,
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
        authorization_labels: Sequence[str],
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]: ...


class RelevanceScorer(Protocol):
    """Score candidates for a query, one value in [0, 1] per candidate, in candidate order.

    Implementations may use statistics of the candidates they are given, so a caller must pass only
    evidence the requester is authorized to see; scores must not depend on anything else.
    """

    def score(self, query: str, candidates: Sequence[RetrievedItem]) -> Sequence[float]: ...


class Chunker(Protocol):
    """Split one source version into scoped, attributable evidence chunks.

    Chunk identifiers must be deterministic functions of tenant scope, source identity, source
    version, and structural path. Authorization labels and content are not part of identity.
    """

    def chunk(
        self,
        *,
        source: SourceReference,
        content: str,
        tenant_scope: str,
        authorization_labels: Sequence[str],
    ) -> Sequence[RetrievedItem]: ...


class TokenEstimator(Protocol):
    """Count tokens deterministically; a budget is hard only relative to its estimator."""

    def count(self, text: str) -> int: ...


class ContextPolicy(Protocol):
    """Select instructions, history and evidence within a hard budget.

    Mandatory content (instructions and the current request) is never trimmed: if it cannot fit,
    the policy raises ``ContextBudgetError``. Evidence is always labeled untrusted data.
    """

    def build(
        self,
        *,
        instructions: Sequence[Message],
        history: Sequence[Message],
        evidence: Sequence[RetrievedItem],
        maximum_tokens: int,
    ) -> ContextPlan: ...


class AnswerabilityPolicy(Protocol):
    def assess(
        self,
        *,
        evidence: Sequence[RetrievedItem],
        now: datetime,
        retrieval_attempt: int,
    ) -> AnswerabilityDecision: ...


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
