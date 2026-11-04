"""Define framework-neutral values shared by contracts and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class ToolRiskTier(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    ACT = "act"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting-for-approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IdempotencyStatus(StrEnum):
    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    SUCCEEDED = "succeeded"
    FAILED_BEFORE_EFFECT = "failed-before-effect"
    OUTCOME_UNKNOWN = "outcome-unknown"


class EvidenceFlag(StrEnum):
    CONFLICTING = "conflicting"
    MALICIOUS = "malicious"


class AnswerabilityRoute(StrEnum):
    ANSWER = "answer"
    RETRIEVE_AGAIN = "retrieve-again"
    CLARIFY = "clarify"
    CAVEAT = "caveat"
    ABSTAIN = "abstain"
    ESCALATE = "escalate"


class AnswerabilityReason(StrEnum):
    SUFFICIENT = "sufficient"
    MISSING = "missing"
    WEAK = "weak"
    STALE = "stale"
    CONFLICTING = "conflicting"
    MALICIOUS = "malicious"


@dataclass(frozen=True, slots=True)
class IdentityScope:
    tenant_scope: str
    subject_scope: str


@dataclass(frozen=True, slots=True)
class RequestContext:
    identity: IdentityScope
    run_id: str
    correlation_id: str
    trace_id: str
    deadline: datetime


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str
    source_label: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, JsonValue]
    risk_tier: ToolRiskTier


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ModelRequest:
    context: RequestContext
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...]
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ModelResponse:
    message: Message | None
    tool_calls: tuple[ToolCall, ...]
    usage: TokenUsage
    model_id: str
    finish_reason: str


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_id: str
    source_version: str
    structural_path: str
    ingested_at: datetime
    fresh_until: datetime


@dataclass(frozen=True, slots=True)
class RetrievedItem:
    item_id: str
    content: str
    score: float
    source: SourceReference
    tenant_scope: str
    authorization_labels: tuple[str, ...]
    retrieved_at: datetime | None = None
    evidence_flags: tuple[EvidenceFlag, ...] = ()
    source_label: str = "untrusted-retrieved-content"


@dataclass(frozen=True, slots=True)
class Citation:
    source_id: str
    source_version: str
    structural_path: str


@dataclass(frozen=True, slots=True)
class AnswerabilityDecision:
    route: AnswerabilityRoute
    reasons: tuple[AnswerabilityReason, ...]
    citations: tuple[Citation, ...] = ()


class ContextRoute(StrEnum):
    FITS = "fits"
    TRIMMED = "trimmed"


@dataclass(frozen=True, slots=True)
class ContextPlan:
    """Messages within a hard budget plus handles for anything left out.

    Omitted and rejected evidence are reported by identifier only, never by content, so callers
    can fetch omitted items by handle and audit counts without copying payloads.
    """

    messages: tuple[Message, ...]
    estimated_tokens: int
    included_evidence_ids: tuple[str, ...]
    omitted_evidence_ids: tuple[str, ...]
    rejected_evidence_ids: tuple[str, ...]
    omitted_history_count: int
    route: ContextRoute


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    record_id: str
    identity: IdentityScope
    kind: str
    value: JsonValue
    provenance: str
    sensitivity: str
    policy_version: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RunState:
    context: RequestContext
    status: RunStatus
    version: str
    updated_at: datetime
    values: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    identity: IdentityScope
    value: JsonValue
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: str
    context: RequestContext
    tool_name: str
    arguments_ref: str
    digest: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Checkpoint:
    checkpoint_id: str
    context: RequestContext
    proposal_ref: str
    proposal_digest: str
    approval_ref: str
    state_version: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    context: RequestContext
    proposal_ref: str
    proposal_digest: str
    tool_name: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    approval_id: str
    proposal_ref: str
    proposal_digest: str
    decision: ApprovalDecision
    decided_by: IdentityScope
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    value: JsonValue
    artifact_ref: str | None = None
    source_label: str = "untrusted-tool-output"


@dataclass(frozen=True, slots=True)
class PendingAction:
    proposal_id: str
    checkpoint_id: str
    approval_id: str
    proposal_digest: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    identity: IdentityScope
    proposal_ref: str
    status: IdempotencyStatus
    result: ToolResult | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunEvent:
    schema_version: int
    event_id: str
    event_type: str
    occurred_at: datetime
    sequence: int
    context: RequestContext
    source: str
    outcome: str
    attributes: dict[str, JsonScalar | list[JsonScalar]]
