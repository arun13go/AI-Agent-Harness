"""Expose the governed local read path as a command-line application."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.adapters.tools.service_status import ServiceStatusTool
from agent_harness.app.local_runner import LocalReadRunner
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.contracts import Tool
from agent_harness.core.errors import HarnessError
from agent_harness.core.registry import Registry
from agent_harness.core.types import IdentityScope, RequestContext, ToolCall


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
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
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


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = _build_parser().parse_args(argv)
    try:
        output = asyncio.run(_run(args))
    except HarnessError as error:
        print(json.dumps({"error": error.code, "message": error.message}), file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0
