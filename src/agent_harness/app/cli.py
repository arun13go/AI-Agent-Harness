"""Expose the governed local read path as a command-line application."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_harness.adapters.foundry.model_client import create_foundry_chat_client
from agent_harness.adapters.maf.agent_executor import MafAgentExecutor
from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.retrieval.lexical import IdfCoverageScorer
from agent_harness.adapters.retrieval.markdown_corpus import load_markdown_corpus
from agent_harness.adapters.storage.in_memory_action_stores import (
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
from agent_harness.app.action_lifecycle import ActionLifecycle
from agent_harness.app.grounded_executor import (
    create_grounded_executor,
    ensure_grounded_profile_supported,
)
from agent_harness.app.read_executor import ReadToolExecutor
from agent_harness.core.configuration import HarnessConfig, ProfileName, load_harness_config
from agent_harness.core.contracts import Tool
from agent_harness.core.errors import ConfigurationError, HarnessError
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    ApprovalDecision,
    IdentityScope,
    Message,
    ModelRequest,
    RequestContext,
    ToolCall,
)


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

    agent_parser = subparsers.add_parser(
        "agent-run",
        help="Run the governed MAF agent against a Foundry model",
    )
    agent_parser.add_argument("--profile", type=Path, required=True)
    agent_parser.add_argument(
        "--config-schema",
        type=Path,
        default=Path("config/schema/v1/harness.schema.json"),
    )
    agent_parser.add_argument(
        "--event-schema",
        type=Path,
        default=Path("config/schema/v1/run-event.schema.json"),
    )
    agent_parser.add_argument("--tenant", required=True)
    agent_parser.add_argument("--subject", required=True)
    agent_parser.add_argument("--prompt", required=True)

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

    grounded_parser = subparsers.add_parser(
        "grounded-run",
        help="Answer a question only from a local Markdown corpus, using a Foundry model",
    )
    grounded_parser.add_argument("--profile", type=Path, required=True)
    grounded_parser.add_argument(
        "--config-schema",
        type=Path,
        default=Path("config/schema/v1/harness.schema.json"),
    )
    grounded_parser.add_argument(
        "--event-schema",
        type=Path,
        default=Path("config/schema/v1/run-event.schema.json"),
    )
    grounded_parser.add_argument("--tenant", required=True)
    grounded_parser.add_argument("--subject", required=True)
    grounded_parser.add_argument("--corpus", type=Path, required=True)
    grounded_parser.add_argument("--question", required=True)
    grounded_parser.add_argument(
        "--corpus-label",
        action="append",
        default=[],
        help="Access label required to read the corpus (repeatable)",
    )
    grounded_parser.add_argument(
        "--caller-label",
        action="append",
        default=[],
        help="Access label the caller holds (repeatable). Development only: hosts must take "
        "labels from authentication",
    )
    grounded_parser.add_argument("--fresh-days", type=int, default=30)
    grounded_parser.add_argument("--minimum-score", type=float, default=0.5)
    return parser


def _require_profile(
    config: HarnessConfig,
    *,
    command: str,
    expected: ProfileName,
) -> None:
    """Reject profile and demo-command combinations that are not composed yet."""

    if config.profile is expected:
        return
    if config.profile is ProfileName.GROUNDED_ASSISTANT:
        raise ConfigurationError(
            f"The grounded-assistant profile runs only with the 'grounded-run' command, "
            f"not '{command}'"
        )
    raise ConfigurationError(
        f"Command '{command}' requires the '{expected.value}' profile; "
        f"received '{config.profile.value}'"
    )


async def _run_read(args: argparse.Namespace) -> dict[str, object]:
    """Compose and execute one local governed run."""

    config = load_harness_config(args.profile, args.config_schema)
    _require_profile(config, command="run", expected=ProfileName.ASSISTANT)
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
    runner = ReadToolExecutor(config=config, tools=tools, audit_sink=audit_sink)
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


async def _run_agent(args: argparse.Namespace) -> dict[str, object]:
    """Compose MAF with Foundry and execute one governed model request."""

    config = load_harness_config(args.profile, args.config_schema)
    _require_profile(config, command="agent-run", expected=ProfileName.ASSISTANT)
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
    client = create_foundry_chat_client(config)
    try:
        response = await MafAgentExecutor(
            config=config,
            client=client,
            tools=tools,
            audit_sink=audit_sink,
        ).run(
            authenticated_identity=identity,
            request=ModelRequest(
                context=context,
                messages=(Message("user", args.prompt, "trusted-user-input"),),
                tools=tuple(tool.definition for _, tool in tools),
                max_output_tokens=1024,
            ),
        )
    finally:
        await client.project_client.close()
    return {
        "runId": context.run_id,
        "response": response.message.content if response.message is not None else None,
        "modelId": response.model_id,
        "finishReason": response.finish_reason,
        "usage": {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
            "cachedInput": response.usage.cached_input_tokens,
        },
        "auditEventCount": len(audit_sink.events),
    }


async def _run_grounded(args: argparse.Namespace) -> dict[str, object]:
    """Load a local corpus and answer one question only from in-scope evidence."""

    config = load_harness_config(args.profile, args.config_schema)
    _require_profile(config, command="grounded-run", expected=ProfileName.GROUNDED_ASSISTANT)
    ensure_grounded_profile_supported(config)
    now = datetime.now(UTC)
    corpus = load_markdown_corpus(
        args.corpus,
        tenant_scope=args.tenant,
        authorization_labels=args.corpus_label,
        ingested_at=now,
        fresh_for=timedelta(days=args.fresh_days),
    )
    if not 0.0 <= args.minimum_score <= 1.0:
        raise ConfigurationError("Minimum score must be between 0 and 1")
    identity = IdentityScope(tenant_scope=args.tenant, subject_scope=args.subject)
    context = RequestContext(
        identity=identity,
        run_id=f"run-{uuid4().hex}",
        correlation_id=f"correlation-{uuid4().hex}",
        trace_id=uuid4().hex,
        deadline=now + timedelta(seconds=config.runtime.timeout_seconds),
    )
    audit_sink = InMemoryAuditSink(args.event_schema)
    client = create_foundry_chat_client(config)
    try:
        executor = create_grounded_executor(
            config=config,
            retrieval=InMemoryRetrievalProvider(corpus, scorer=IdfCoverageScorer()),
            runtime=MafAgentExecutor(
                config=config,
                client=client,
                tools=Registry[Tool]("tool"),
                audit_sink=audit_sink,
            ),
            audit_sink=audit_sink,
            minimum_evidence_score=args.minimum_score,
        )
        response = await executor.run(
            authenticated_identity=identity,
            context=context,
            question=args.question,
            authorization_labels=args.caller_label,
        )
    finally:
        await client.project_client.close()
    return {
        "runId": context.run_id,
        "route": response.route.value,
        "reasons": [reason.value for reason in response.reasons],
        "answer": response.message.content,
        "citations": [
            {
                "sourceId": citation.source_id,
                "sourceVersion": citation.source_version,
                "path": citation.structural_path,
            }
            for citation in response.citations
        ],
        "includedEvidenceCount": len(response.included_evidence_ids),
        "omittedEvidenceCount": len(response.omitted_evidence_ids),
        "retrievalAttempts": response.retrieval_attempts,
        "chunkCount": len(corpus),
        "modelId": response.model.model_id if response.model is not None else None,
        "auditEventCount": len(audit_sink.events),
    }


async def _run_action_demo(args: argparse.Namespace) -> dict[str, object]:
    """Compose and execute one local action lifecycle."""

    config = load_harness_config(args.profile, args.config_schema)
    _require_profile(config, command="action-demo", expected=ProfileName.ACTION_AGENT)
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
    runner = ActionLifecycle(
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
        elif args.command == "agent-run":
            output = asyncio.run(_run_agent(args))
        elif args.command == "grounded-run":
            output = asyncio.run(_run_grounded(args))
        else:
            output = asyncio.run(_run_read(args))
    except HarnessError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
