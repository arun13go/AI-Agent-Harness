"""Create stable proposal and idempotency bindings for action execution."""

from __future__ import annotations

import hashlib
import json

from agent_harness.core.types import ActionProposal, JsonValue


def proposal_digest(
    *,
    tenant_scope: str,
    subject_scope: str,
    run_id: str,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> str:
    """Hash the exact identity, run, tool, and arguments being approved."""

    canonical = json.dumps(
        {
            "tenantScope": tenant_scope,
            "subjectScope": subject_scope,
            "runId": run_id,
            "toolName": tool_name,
            "arguments": arguments,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_key(proposal: ActionProposal) -> str:
    """Derive one stable execution key from a bound proposal."""

    value = (
        f"{proposal.context.identity.tenant_scope}:"
        f"{proposal.proposal_id}:{proposal.tool_name}:{proposal.digest}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
