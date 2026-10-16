"""Verify approval binding, replay safety, and action lifecycle outcomes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from agent_harness.adapters.storage.in_memory_action_stores import (
    InMemoryApprovalProvider,
    InMemoryArtifactStore,
    InMemoryCheckpointStore,
    InMemoryIdempotencyLedger,
    InMemoryProposalStore,
    InMemoryRunStateStore,
)
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.adapters.tools.maintenance_request import (
    MaintenanceProposalTool,
    MaintenanceRequestTool,
)
from agent_harness.app.action_lifecycle import ActionLifecycle
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.contracts import Tool
from agent_harness.core.errors import (
    ActionInProgressError,
    ActionOutcomeUnknownError,
    ApprovalError,
)
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    ApprovalDecision,
    IdentityScope,
    JsonValue,
    RequestContext,
    ToolCall,
    ToolResult,
)

ROOT = Path(__file__).parents[2]
CONFIG_SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"


@dataclass
class MutableClock:
    """Provide deterministic time and expiry control."""

    value: datetime

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        """Advance the current test time."""

        self.value += delta


@dataclass
class ActionSystem:
    """Group the action runner and observable test adapters."""

    runner: ActionLifecycle
    tool: MaintenanceRequestTool
    artifacts: InMemoryArtifactStore
    proposals: InMemoryProposalStore
    run_states: InMemoryRunStateStore
    audit: InMemoryAuditSink
    clock: MutableClock
    context: RequestContext


def build_system(
    *,
    lose_response: bool = False,
    approval_ttl: timedelta = timedelta(minutes=15),
) -> ActionSystem:
    """Build one isolated action lifecycle system."""

    config = load_harness_config(
        ROOT / "config" / "profiles" / "action-agent.yaml",
        CONFIG_SCHEMA,
    )
    tool = MaintenanceRequestTool(
        request_id_factory=lambda: "request-0001",
        lose_response=lose_response,
    )
    tools = Registry[Tool]("tool")
    tools.register(tool.definition.name, tool)
    artifacts = InMemoryArtifactStore()
    proposals = InMemoryProposalStore()
    audit = InMemoryAuditSink(EVENT_SCHEMA)
    clock = MutableClock(datetime(2026, 10, 12, 9, 0, tzinfo=UTC))
    context = RequestContext(
        identity=IdentityScope("tenant-0001", "subject-0001"),
        run_id="run-00000001",
        correlation_id="correlation-0001",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=clock() + timedelta(hours=1),
    )
    run_states = InMemoryRunStateStore()
    runner = ActionLifecycle(
        config=config,
        tools=tools,
        artifacts=artifacts,
        proposals=proposals,
        checkpoints=InMemoryCheckpointStore(),
        approvals=InMemoryApprovalProvider(),
        run_states=run_states,
        idempotency=InMemoryIdempotencyLedger(),
        audit_sink=audit,
        approval_ttl=approval_ttl,
        clock=clock,
    )
    return ActionSystem(
        runner,
        tool,
        artifacts,
        proposals,
        run_states,
        audit,
        clock,
        context,
    )


async def propose(system: ActionSystem):
    """Create the standard maintenance-request proposal."""

    return await system.runner.propose(
        authenticated_identity=system.context.identity,
        context=system.context,
        call=ToolCall(
            call_id="call-00000001",
            tool_name=system.tool.definition.name,
            arguments={"title": "Patch development service"},
        ),
    )


async def approve(system: ActionSystem, approval_id: str) -> None:
    """Approve a proposal from another subject in the same tenant."""

    result = await system.runner.decide(
        approver_identity=IdentityScope("tenant-0001", "approver-0001"),
        approval_id=approval_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert result.decision is ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_action_cannot_run_before_approval() -> None:
    system = build_system()
    pending = await propose(system)

    with pytest.raises(ApprovalError, match="does not have an approval"):
        await system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        )

    assert system.tool.effect_count == 0


@pytest.mark.asyncio
async def test_proposal_tool_creates_review_without_action_effect() -> None:
    system = build_system()
    proposal_tool = MaintenanceProposalTool(system.runner)

    result = await proposal_tool.invoke(
        context=system.context,
        call=ToolCall(
            call_id="call-00000001",
            tool_name=proposal_tool.definition.name,
            arguments={"title": "Patch development service"},
        ),
        idempotency_key=None,
    )

    assert isinstance(result.value, dict)
    assert result.value["status"] == "waiting-for-approval"
    assert system.tool.effect_count == 0


@pytest.mark.asyncio
async def test_approved_action_executes_once_and_replays_result() -> None:
    system = build_system()
    pending = await propose(system)
    await approve(system, pending.approval_id)

    first = await system.runner.resume(
        authenticated_identity=system.context.identity,
        checkpoint_id=pending.checkpoint_id,
    )
    replay = await system.runner.resume(
        authenticated_identity=system.context.identity,
        checkpoint_id=pending.checkpoint_id,
    )

    assert first == replay
    assert system.tool.effect_count == 1
    assert [event.event_type for event in system.audit.events] == [
        "run.started",
        "tool.proposed",
        "approval.requested",
        "checkpoint.created",
        "approval.decided",
        "run.resumed",
        "tool.started",
        "tool.completed",
        "run.completed",
    ]


@pytest.mark.asyncio
async def test_denied_and_expired_approvals_block_execution() -> None:
    denied = build_system()
    denied_pending = await propose(denied)
    result = await denied.runner.decide(
        approver_identity=IdentityScope("tenant-0001", "approver-0001"),
        approval_id=denied_pending.approval_id,
        decision=ApprovalDecision.DENIED,
    )
    assert result.decision is ApprovalDecision.DENIED
    with pytest.raises(ApprovalError):
        await denied.runner.resume(
            authenticated_identity=denied.context.identity,
            checkpoint_id=denied_pending.checkpoint_id,
        )
    assert denied.tool.effect_count == 0

    expired = build_system(approval_ttl=timedelta(seconds=1))
    expired_pending = await propose(expired)
    expired.clock.advance(timedelta(seconds=2))
    result = await expired.runner.decide(
        approver_identity=IdentityScope("tenant-0001", "approver-0001"),
        approval_id=expired_pending.approval_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert result.decision is ApprovalDecision.EXPIRED
    with pytest.raises(ApprovalError):
        await expired.runner.resume(
            authenticated_identity=expired.context.identity,
            checkpoint_id=expired_pending.checkpoint_id,
        )
    assert expired.tool.effect_count == 0


@pytest.mark.asyncio
async def test_cross_tenant_approval_is_rejected() -> None:
    system = build_system()
    pending = await propose(system)

    with pytest.raises(ApprovalError, match="approver tenant"):
        await system.runner.decide(
            approver_identity=IdentityScope("tenant-0002", "approver-0001"),
            approval_id=pending.approval_id,
            decision=ApprovalDecision.APPROVED,
        )

    assert system.tool.effect_count == 0


@pytest.mark.asyncio
async def test_changed_proposal_is_rejected_after_approval() -> None:
    system = build_system()
    pending = await propose(system)
    await approve(system, pending.approval_id)
    proposal = await system.proposals.load(
        identity=system.context.identity,
        proposal_id=pending.proposal_id,
    )
    assert proposal is not None
    artifact = await system.artifacts.load(
        identity=system.context.identity,
        artifact_id=proposal.arguments_ref,
    )
    assert artifact is not None and isinstance(artifact.value, dict)
    arguments = cast(dict[str, JsonValue], artifact.value)
    arguments["title"] = "Changed after approval"

    with pytest.raises(ApprovalError, match="changed"):
        await system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        )

    assert system.tool.effect_count == 0


@pytest.mark.asyncio
async def test_stale_checkpoint_is_rejected() -> None:
    system = build_system()
    pending = await propose(system)
    await approve(system, pending.approval_id)
    state = await system.run_states.load(
        identity=system.context.identity,
        run_id=system.context.run_id,
    )
    assert state is not None
    await system.run_states.save(
        replace(state, updated_at=system.clock()),
        expected_version=state.version,
    )

    with pytest.raises(ApprovalError, match="current run state"):
        await system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        )

    assert system.tool.effect_count == 0


@pytest.mark.asyncio
async def test_concurrent_resume_never_repeats_effect() -> None:
    system = build_system()
    pending = await propose(system)
    await approve(system, pending.approval_id)

    outcomes = await asyncio.gather(
        system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        ),
        system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        ),
        return_exceptions=True,
    )

    assert system.tool.effect_count == 1
    results = [outcome for outcome in outcomes if isinstance(outcome, ToolResult)]
    in_progress = [outcome for outcome in outcomes if isinstance(outcome, ActionInProgressError)]
    assert len(results) + len(in_progress) == 2
    assert not results or all(result == results[0] for result in results)


@pytest.mark.asyncio
async def test_unknown_outcome_blocks_automatic_retry() -> None:
    system = build_system(lose_response=True)
    pending = await propose(system)
    await approve(system, pending.approval_id)

    with pytest.raises(ActionOutcomeUnknownError):
        await system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        )
    with pytest.raises(ActionOutcomeUnknownError, match="retry is blocked"):
        await system.runner.resume(
            authenticated_identity=system.context.identity,
            checkpoint_id=pending.checkpoint_id,
        )

    assert system.tool.effect_count == 1
