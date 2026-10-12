"""Verify stable action proposal and idempotency bindings."""

from datetime import UTC, datetime, timedelta

from agent_harness.core.action import idempotency_key, proposal_digest
from agent_harness.core.types import ActionProposal, IdentityScope, RequestContext


def test_proposal_digest_changes_with_arguments() -> None:
    first = proposal_digest(
        tenant_scope="tenant-0001",
        subject_scope="subject-0001",
        run_id="run-00000001",
        tool_name="create_maintenance_request",
        arguments={"title": "First"},
    )
    second = proposal_digest(
        tenant_scope="tenant-0001",
        subject_scope="subject-0001",
        run_id="run-00000001",
        tool_name="create_maintenance_request",
        arguments={"title": "Second"},
    )

    assert first != second


def test_idempotency_key_is_stable_for_proposal() -> None:
    now = datetime.now(UTC)
    proposal = ActionProposal(
        proposal_id="proposal-0001",
        context=RequestContext(
            identity=IdentityScope("tenant-0001", "subject-0001"),
            run_id="run-00000001",
            correlation_id="correlation-0001",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=now + timedelta(minutes=1),
        ),
        tool_name="create_maintenance_request",
        arguments_ref="artifact-0001",
        digest="a" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=1),
    )

    assert idempotency_key(proposal) == idempotency_key(proposal)
