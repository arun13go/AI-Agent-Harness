"""Expose the governed local read path as a command-line application."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_harness.adapters.storage.in_memory_action import (
    InMemoryApprovalProvider,
    InMemoryArtifactStore,
    InMemoryCheckpointStore,
    InMemoryIdempotencyLedger,
    InMemoryProposalStore,
    InMemoryRunStateStore,
)
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.adapters.tools.maintenance_request import MaintenanceRequestTool
from agent_harness.adapters.tools.service_status import ServiceStatusTool
from agent_harness.app.action_runner import ActionRunner
from agent_harness.app.local_runner import LocalReadRunner
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.contracts import Tool
from agent_harness.core.errors import HarnessError
from agent_harness.core.registry import Registry
from agent_harness.core.types import ApprovalDecision, IdentityScope, RequestContext, ToolCall


def _build_parser() -> argparse.ArgumentParser:
    """Build command-line arguments for local execution."""

    parser = argparse.ArgumentParser(prog="agent-harness")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the deterministic local read path")
    run_parser.add_argument("--profile", type=Path, required=True)
    run_parser.add_argument(
        "--config-schema",
        type=Path,
        default=Path("config/schema/v1/harness.schema.json"),
    )
    run_parser.add_argument(
        "--event-schema",
        type=Path,
        default=Path("config/schema/v1/run-event.schema.json"),
    )
    run_parser.add_argument("--tenant", required=True)
    run_parser.add_argument("--subject", required=True)
    run_parser.add_argument("--service", default="api")

    action_parser = subparsers.add_parser(
        "action-demo",
        help="Run the local proposal, approval, and action lifecycle",
    )
    action_parser.add_argument("--profile", type=Path, required=True)
    action_parser.add_argument(
        "--config-schema",
        type=Path,
        default=Path("config/schema/v1/harness.schema.json"),
    )
    action_parser.add_argument(
        "--event-schema",
        type=Path,
        default=Path("config/schema/v1/run-event.schema.json"),
    )
    action_parser.add_argument("--tenant", required=True)
    action_parser.add_argument("--subject", required=True)
    action_parser.add_argument("--approver", required=True)
    action_parser.add_argument("--title", default="Patch development service")
    action_parser.add_argument(
        "--decision",
        choices=[ApprovalDecision.APPROVED.value, ApprovalDecision.DENIED.value],
        default=ApprovalDecision.APPROVED.value,
    )
    return parser


async def _run_read(args: argparse.Namespace) -> dict[str, object]:
    """Compose and execute one local governed run."""

    config = load_harness_config(args.profile, args.config_schema)
    identity = IdentityScope(tenant_scope=args.tenant, subject_scope=args.subject)
    context = RequestContext(
        identity=identity,
        run_id=f"run-{uuid4().hex}",
        correlation_id=f"correlation-{uuid4().hex}",
        trace_id=uuid4().hex,
        deadline=datetime.now(UTC) + timedelta(seconds=config.runtime.timeout_seconds),
    )
    tools = Registry[Tool]("tool")
    tools.register(
        "get_service_status",
        ServiceStatusTool(
            {
                args.tenant: {
                    "api": "healthy",
                    "worker": "degraded",
                }
            }
        ),
    )
    audit_sink = InMemoryAuditSink(args.event_schema)
    runner = LocalReadRunner(config=config, tools=tools, audit_sink=audit_sink)
    result = await runner.run(
        authenticated_identity=identity,
        context=context,
        call=ToolCall(
            call_id=f"call-{uuid4().hex}",
            tool_name="get_service_status",
            arguments={"service": args.service},
        ),
    )
    return {
        "runId": context.run_id,
        "result": result.value,
        "sourceLabel": result.source_label,
        "auditEventCount": len(audit_sink.events),
    }


async def _run_action_demo(args: argparse.Namespace) -> dict[str, object]:
    """Compose and execute one local action lifecycle."""

    config = load_harness_config(args.profile, args.config_schema)
    identity = IdentityScope(tenant_scope=args.tenant, subject_scope=args.subject)
    context = RequestContext(
        identity=identity,
        run_id=f"run-{uuid4().hex}",
        correlation_id=f"correlation-{uuid4().hex}",
        trace_id=uuid4().hex,
        deadline=datetime.now(UTC) + timedelta(seconds=config.runtime.timeout_seconds),
    )
    tool = MaintenanceRequestTool()
    tools = Registry[Tool]("tool")
    tools.register(tool.definition.name, tool)
    audit_sink = InMemoryAuditSink(args.event_schema)
    runner = ActionRunner(
        config=config,
        tools=tools,
        artifacts=InMemoryArtifactStore(),
        proposals=InMemoryProposalStore(),
        checkpoints=InMemoryCheckpointStore(),
        approvals=InMemoryApprovalProvider(),
        run_states=InMemoryRunStateStore(),
        idempotency=InMemoryIdempotencyLedger(),
        audit_sink=audit_sink,
    )
    pending = await runner.propose(
        authenticated_identity=identity,
        context=context,
        call=ToolCall(
            call_id=f"call-{uuid4().hex}",
            tool_name=tool.definition.name,
            arguments={"title": args.title},
        ),
    )
    decision = await runner.decide(
        approver_identity=IdentityScope(
            tenant_scope=args.tenant,
            subject_scope=args.approver,
        ),
        approval_id=pending.approval_id,
        decision=ApprovalDecision(args.decision),
    )
    result = None
    if decision.decision is ApprovalDecision.APPROVED:
        result = await runner.resume(
            authenticated_identity=identity,
            checkpoint_id=pending.checkpoint_id,
        )
    return {
        "runId": context.run_id,
        "proposalId": pending.proposal_id,
        "approvalId": pending.approval_id,
        "decision": decision.decision.value,
        "result": result.value if result is not None else None,
        "effectCount": tool.effect_count,
        "auditEventCount": len(audit_sink.events),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "action-demo":
            output = asyncio.run(_run_action_demo(args))
        else:
            output = asyncio.run(_run_read(args))
    except HarnessError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
