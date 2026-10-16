"""Coordinate proposal, approval, resume, and idempotent action execution."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from agent_harness.core.action_binding import idempotency_key, proposal_digest
from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.contracts import (
    ApprovalProvider,
    ArtifactStore,
    AuditSink,
    CheckpointStore,
    IdempotencyLedger,
    ProposalStore,
    RunStateStore,
    Tool,
)
from agent_harness.core.errors import (
    ActionInProgressError,
    ActionLifecycleError,
    ActionOutcomeUnknownError,
    ApprovalError,
    HarnessError,
)
from agent_harness.core.event_writer import RunEventWriter
from agent_harness.core.governance import (
    authorize_tool,
    validate_identity_scope,
    validate_request_context,
    validate_tool_call,
)
from agent_harness.core.registry import Registry
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
    JsonValue,
    PendingAction,
    RequestContext,
    RunState,
    RunStatus,
    ToolCall,
    ToolResult,
    ToolRiskTier,
)


class ActionLifecycle:
    """Enforce approval binding and exactly-once action coordination."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        tools: Registry[Tool],
        artifacts: ArtifactStore,
        proposals: ProposalStore,
        checkpoints: CheckpointStore,
        approvals: ApprovalProvider,
        run_states: RunStateStore,
        idempotency: IdempotencyLedger,
        audit_sink: AuditSink,
        approval_ttl: timedelta = timedelta(minutes=15),
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._config = config
        self._tools = tools
        self._artifacts = artifacts
        self._proposals = proposals
        self._checkpoints = checkpoints
        self._approvals = approvals
        self._run_states = run_states
        self._idempotency = idempotency
        self._audit_sink = audit_sink
        self._approval_ttl = approval_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    async def propose(
        self,
        *,
        authenticated_identity: IdentityScope,
        context: RequestContext,
        call: ToolCall,
    ) -> PendingAction:
        """Validate and checkpoint one act-tier proposal for approval."""

        now = self._clock()
        validate_request_context(authenticated_identity, context, now=now)
        tool = self._tools.resolve(call.tool_name)
        authorize_tool(
            ToolRiskTier(self._config.governance.maximum_tier.value),
            tool.definition,
        )
        if tool.definition.risk_tier is not ToolRiskTier.ACT:
            raise ActionLifecycleError("Action proposals require an act-tier tool")
        validate_tool_call(tool.definition, call)

        expires_at = min(context.deadline, now + self._approval_ttl)
        artifact_id = f"artifact-{self._id_factory()}"
        proposal_id = f"proposal-{self._id_factory()}"
        approval_id = f"approval-{self._id_factory()}"
        checkpoint_id = f"checkpoint-{self._id_factory()}"
        digest = proposal_digest(
            tenant_scope=context.identity.tenant_scope,
            subject_scope=context.identity.subject_scope,
            run_id=context.run_id,
            tool_name=call.tool_name,
            arguments=call.arguments,
        )
        await self._artifacts.save(
            ArtifactRecord(
                artifact_id=artifact_id,
                identity=context.identity,
                value=dict(call.arguments),
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self._proposals.save(
            ActionProposal(
                proposal_id=proposal_id,
                context=context,
                tool_name=call.tool_name,
                arguments_ref=artifact_id,
                digest=digest,
                created_at=now,
                expires_at=expires_at,
            )
        )
        state = await self._run_states.save(
            RunState(
                context=context,
                status=RunStatus.WAITING_FOR_APPROVAL,
                version="",
                updated_at=now,
                values={
                    "proposalRef": proposal_id,
                    "approvalRef": approval_id,
                    "checkpointRef": checkpoint_id,
                },
            ),
            expected_version=None,
        )
        await self._checkpoints.save(
            Checkpoint(
                checkpoint_id=checkpoint_id,
                context=context,
                proposal_ref=proposal_id,
                proposal_digest=digest,
                approval_ref=approval_id,
                state_version=state.version,
                created_at=now,
                expires_at=expires_at,
            )
        )
        await self._approvals.request(
            ApprovalRequest(
                approval_id=approval_id,
                context=context,
                proposal_ref=proposal_id,
                proposal_digest=digest,
                tool_name=call.tool_name,
                expires_at=expires_at,
            )
        )

        writer = self._writer(context)
        await writer.emit("run.started", source="app", outcome="started", attributes={})
        await writer.emit(
            "tool.proposed",
            source="tool",
            outcome="pending-approval",
            attributes={
                "toolName": call.tool_name,
                "proposalId": proposal_id,
                "riskTier": ToolRiskTier.ACT.value,
            },
        )
        await writer.emit(
            "approval.requested",
            source="approval",
            outcome="pending-approval",
            attributes={"approvalId": approval_id, "proposalId": proposal_id},
        )
        await writer.emit(
            "checkpoint.created",
            source="state",
            outcome="succeeded",
            attributes={"checkpointId": checkpoint_id, "proposalId": proposal_id},
        )
        return PendingAction(
            proposal_id=proposal_id,
            checkpoint_id=checkpoint_id,
            approval_id=approval_id,
            proposal_digest=digest,
        )

    async def decide(
        self,
        *,
        approver_identity: IdentityScope,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalResult:
        """Record one tenant-bound approval decision."""

        validate_identity_scope(approver_identity)
        request = await self._approvals.get_request(
            tenant_scope=approver_identity.tenant_scope,
            approval_id=approval_id,
        )
        if request is None:
            raise ApprovalError("Approval request was not found in the approver tenant")
        now = self._clock()
        effective_decision = decision
        if request.expires_at <= now:
            effective_decision = ApprovalDecision.EXPIRED
        result = ApprovalResult(
            approval_id=approval_id,
            proposal_ref=request.proposal_ref,
            proposal_digest=request.proposal_digest,
            decision=effective_decision,
            decided_by=approver_identity,
            decided_at=now,
        )
        await self._approvals.decide(result)
        writer = self._writer(request.context)
        outcome = "succeeded"
        if effective_decision is not ApprovalDecision.APPROVED:
            outcome = "denied"
        await writer.emit(
            "approval.decided",
            source="approval",
            outcome=outcome,
            attributes={
                "approvalId": approval_id,
                "proposalId": request.proposal_ref,
                "decision": effective_decision.value,
            },
        )
        if effective_decision is not ApprovalDecision.APPROVED:
            await self._finish_denied(request.context, writer)
        return result

    async def resume(
        self,
        *,
        authenticated_identity: IdentityScope,
        checkpoint_id: str,
    ) -> ToolResult:
        """Resume an approved checkpoint without repeating its effect."""

        validate_identity_scope(authenticated_identity)
        checkpoint = await self._checkpoints.load(
            identity=authenticated_identity,
            checkpoint_id=checkpoint_id,
        )
        if checkpoint is None:
            raise ApprovalError("Checkpoint was not found in the authenticated identity scope")
        context = checkpoint.context
        writer = self._writer(context)
        proposal, arguments = await self._load_bound_proposal(checkpoint)
        key = idempotency_key(proposal)
        existing = await self._idempotency.load(identity=context.identity, key=key)
        if existing is not None:
            return self._replay(existing)

        now = self._clock()
        if checkpoint.expires_at <= now or proposal.expires_at <= now:
            raise ApprovalError("Proposal or checkpoint has expired")
        approval = await self._approvals.get(
            identity=context.identity,
            approval_id=checkpoint.approval_ref,
        )
        if approval is None:
            raise ApprovalError("Action does not have an approval decision")
        if approval.decision is not ApprovalDecision.APPROVED:
            raise ApprovalError(f"Action approval is '{approval.decision.value}'")
        if (
            approval.proposal_ref != proposal.proposal_id
            or approval.proposal_digest != proposal.digest
        ):
            raise ApprovalError("Approval is not bound to the current proposal")

        state = await self._run_states.load(
            identity=context.identity,
            run_id=context.run_id,
        )
        if (
            state is None
            or state.status is not RunStatus.WAITING_FOR_APPROVAL
            or state.version != checkpoint.state_version
        ):
            raise ApprovalError("Checkpoint does not match the current run state")

        claim = IdempotencyRecord(
            key=key,
            identity=context.identity,
            proposal_ref=proposal.proposal_id,
            status=IdempotencyStatus.IN_PROGRESS,
            result=None,
            updated_at=now,
        )
        if not await self._idempotency.claim(claim):
            existing = await self._idempotency.load(identity=context.identity, key=key)
            if existing is None:
                raise ActionInProgressError("Action claim changed during resume")
            return self._replay(existing)

        running_state = await self._run_states.save(
            replace(state, status=RunStatus.RUNNING, updated_at=now),
            expected_version=state.version,
        )
        tool = self._tools.resolve(proposal.tool_name)
        authorize_tool(
            ToolRiskTier(self._config.governance.maximum_tier.value),
            tool.definition,
        )
        call = ToolCall(
            call_id=f"call-{proposal.proposal_id}",
            tool_name=proposal.tool_name,
            arguments=arguments,
        )
        validate_tool_call(tool.definition, call)
        await writer.emit(
            "run.resumed",
            source="state",
            outcome="started",
            attributes={
                "checkpointId": checkpoint.checkpoint_id,
                "proposalId": proposal.proposal_id,
            },
        )
        await writer.emit(
            "tool.started",
            source="tool",
            outcome="started",
            attributes={"toolName": proposal.tool_name, "proposalId": proposal.proposal_id},
        )
        try:
            remaining = (context.deadline - self._clock()).total_seconds()
            timeout_seconds = min(float(self._config.runtime.timeout_seconds), remaining)
            if timeout_seconds <= 0:
                raise TimeoutError
            async with asyncio.timeout(timeout_seconds):
                result = await tool.invoke(
                    context=context,
                    call=call,
                    idempotency_key=key,
                )
        except ActionOutcomeUnknownError:
            await self._complete_failure(
                claim,
                running_state,
                writer,
                IdempotencyStatus.OUTCOME_UNKNOWN,
                "action_outcome_unknown",
            )
            raise
        except TimeoutError as error:
            await self._complete_failure(
                claim,
                running_state,
                writer,
                IdempotencyStatus.OUTCOME_UNKNOWN,
                "action_outcome_unknown",
            )
            raise ActionOutcomeUnknownError(
                "Action timed out; its outcome is unknown and retry is blocked"
            ) from error
        except HarnessError as error:
            await self._complete_failure(
                claim,
                running_state,
                writer,
                IdempotencyStatus.FAILED_BEFORE_EFFECT,
                error.code,
            )
            raise
        except Exception as error:
            await self._complete_failure(
                claim,
                running_state,
                writer,
                IdempotencyStatus.OUTCOME_UNKNOWN,
                "action_outcome_unknown",
            )
            raise ActionOutcomeUnknownError(
                "Action outcome is unknown; automatic retry is blocked"
            ) from error

        await self._idempotency.complete(
            replace(
                claim,
                status=IdempotencyStatus.SUCCEEDED,
                result=result,
                updated_at=self._clock(),
            )
        )
        await self._run_states.save(
            replace(
                running_state,
                status=RunStatus.COMPLETED,
                updated_at=self._clock(),
            ),
            expected_version=running_state.version,
        )
        await writer.emit(
            "tool.completed",
            source="tool",
            outcome="succeeded",
            attributes={"toolName": proposal.tool_name, "proposalId": proposal.proposal_id},
        )
        await writer.emit("run.completed", source="app", outcome="succeeded", attributes={})
        return result

    async def _load_bound_proposal(
        self, checkpoint: Checkpoint
    ) -> tuple[ActionProposal, dict[str, JsonValue]]:
        proposal = await self._proposals.load(
            identity=checkpoint.context.identity,
            proposal_id=checkpoint.proposal_ref,
        )
        if proposal is None:
            raise ApprovalError("Checkpoint proposal does not exist")
        artifact = await self._artifacts.load(
            identity=checkpoint.context.identity,
            artifact_id=proposal.arguments_ref,
        )
        if artifact is None or not isinstance(artifact.value, dict):
            raise ApprovalError("Proposal arguments artifact is unavailable")
        arguments = cast(dict[str, JsonValue], artifact.value)
        current_digest = proposal_digest(
            tenant_scope=proposal.context.identity.tenant_scope,
            subject_scope=proposal.context.identity.subject_scope,
            run_id=proposal.context.run_id,
            tool_name=proposal.tool_name,
            arguments=arguments,
        )
        if current_digest != proposal.digest or current_digest != checkpoint.proposal_digest:
            raise ApprovalError("Proposal changed after approval was requested")
        return proposal, arguments

    def _replay(self, record: IdempotencyRecord) -> ToolResult:
        if record.status is IdempotencyStatus.SUCCEEDED and record.result is not None:
            return record.result
        if record.status is IdempotencyStatus.IN_PROGRESS:
            raise ActionInProgressError("Action execution is already in progress")
        if record.status is IdempotencyStatus.OUTCOME_UNKNOWN:
            raise ActionOutcomeUnknownError("Action outcome is unknown; retry is blocked")
        raise ActionLifecycleError(f"Action cannot resume from '{record.status.value}'")

    async def _finish_denied(
        self,
        context: RequestContext,
        writer: RunEventWriter,
    ) -> None:
        state = await self._run_states.load(
            identity=context.identity,
            run_id=context.run_id,
        )
        if state is not None and state.status is RunStatus.WAITING_FOR_APPROVAL:
            await self._run_states.save(
                replace(
                    state,
                    status=RunStatus.CANCELLED,
                    updated_at=self._clock(),
                ),
                expected_version=state.version,
            )
        await writer.emit("run.failed", source="app", outcome="denied", attributes={})

    async def _complete_failure(
        self,
        claim: IdempotencyRecord,
        state: RunState,
        writer: RunEventWriter,
        status: IdempotencyStatus,
        error_code: str,
    ) -> None:
        await self._idempotency.complete(replace(claim, status=status, updated_at=self._clock()))
        await self._run_states.save(
            replace(state, status=RunStatus.FAILED, updated_at=self._clock()),
            expected_version=state.version,
        )
        await writer.emit(
            "tool.failed",
            source="tool",
            outcome="failed",
            attributes={"errorCode": error_code},
        )
        await writer.emit(
            "run.failed",
            source="app",
            outcome="failed",
            attributes={"errorCode": error_code},
        )

    def _writer(self, context: RequestContext) -> RunEventWriter:
        return RunEventWriter(
            context=context,
            audit_sink=self._audit_sink,
            clock=self._clock,
        )
