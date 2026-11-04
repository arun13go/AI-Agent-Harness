"""Verify registry, identity, limits, authorization, and audit policy."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.core.errors import (
    AuditError,
    AuthorizationError,
    DuplicateEventError,
    DuplicateRegistrationError,
    EventSequenceError,
    ExecutionLimitError,
    InputValidationError,
    UnknownRegistrationError,
)
from agent_harness.core.governance import (
    ExecutionBudget,
    authorize_tool,
    validate_authorization_labels,
    validate_request_context,
    validate_source_reference,
    validate_tenant_scope,
    validate_tool_call,
)
from agent_harness.core.registry import Registry
from agent_harness.core.types import (
    IdentityScope,
    JsonScalar,
    RequestContext,
    RunEvent,
    SourceReference,
    ToolCall,
    ToolDefinition,
    ToolRiskTier,
)

ROOT = Path(__file__).parents[2]
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"


def make_context(
    *,
    tenant: str = "tenant-0001",
    subject: str = "subject-0001",
    run_id: str = "run-00000001",
) -> RequestContext:
    """Build a valid request context with optional identity overrides."""

    return RequestContext(
        identity=IdentityScope(tenant_scope=tenant, subject_scope=subject),
        run_id=run_id,
        correlation_id="correlation-0001",
        trace_id="0123456789abcdef0123456789abcdef",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )


def make_event(
    sequence: int = 0,
    *,
    event_id: str = "event-000001",
    context: RequestContext | None = None,
    attributes: dict[str, JsonScalar | list[JsonScalar]] | None = None,
) -> RunEvent:
    """Build a schema-valid run event for audit tests."""

    return RunEvent(
        schema_version=1,
        event_id=event_id,
        event_type="run.started",
        occurred_at=datetime.now(UTC),
        sequence=sequence,
        context=context or make_context(),
        source="app",
        outcome="started",
        attributes=attributes or {},
    )


def test_registry_rejects_duplicate_and_unknown_names() -> None:
    registry = Registry[object]("tool")
    registry.register("status", object())

    with pytest.raises(DuplicateRegistrationError):
        registry.register("status", object())
    with pytest.raises(UnknownRegistrationError, match="missing"):
        registry.resolve("missing")


def test_request_context_rejects_identity_substitution() -> None:
    context = make_context(tenant="tenant-0002")

    with pytest.raises(AuthorizationError):
        validate_request_context(
            IdentityScope(tenant_scope="tenant-0001", subject_scope="subject-0001"),
            context,
        )


def test_authorization_rejects_tool_above_profile_tier() -> None:
    definition = ToolDefinition(
        name="restart_service",
        description="Restart a service",
        input_schema={"type": "object"},
        risk_tier=ToolRiskTier.ACT,
    )

    with pytest.raises(AuthorizationError):
        authorize_tool(ToolRiskTier.PROPOSE, definition)


def test_tool_input_is_validated_against_registered_schema() -> None:
    definition = ToolDefinition(
        name="get_service_status",
        description="Read a service status",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["service"],
            "properties": {"service": {"type": "string", "minLength": 1}},
        },
        risk_tier=ToolRiskTier.READ,
    )

    with pytest.raises(InputValidationError, match="service"):
        validate_tool_call(
            definition,
            ToolCall(call_id="call-00000001", tool_name=definition.name, arguments={}),
        )


@pytest.mark.parametrize(
    "value", ["", "short", "tenant 0001", "tenant/0001", "-tenant-0001", "t" * 129]
)
def test_tenant_scope_must_be_an_opaque_identifier(value: str) -> None:
    with pytest.raises(InputValidationError, match="Tenant scope"):
        validate_tenant_scope(value)
    validate_tenant_scope("tenant-0001")


def test_authorization_labels_are_canonicalized_and_bounded() -> None:
    assert validate_authorization_labels(["group:b", "group:a", "group:b"]) == (
        "group:a",
        "group:b",
    )
    assert validate_authorization_labels([]) == ()
    for invalid in (["bad label"], [""], ["x" * 129], [":leading"]):
        with pytest.raises(InputValidationError, match="Authorization label"):
            validate_authorization_labels(invalid)
    with pytest.raises(InputValidationError, match="At most 64"):
        validate_authorization_labels([f"label-{number}" for number in range(65)])
    assert len(validate_authorization_labels([f"label-{n % 64}" for n in range(500)])) == 64


def test_source_reference_rejects_ambiguous_lineage() -> None:
    moment = datetime(2026, 11, 3, tzinfo=UTC)
    valid = SourceReference("source-1", "v1", "/docs/a.md", moment, moment)
    validate_source_reference(valid)
    invalid = [
        SourceReference("", "v1", "/docs/a.md", moment, moment),
        SourceReference("s" * 257, "v1", "/docs/a.md", moment, moment),
        SourceReference("source-1", "v\n1", "/docs/a.md", moment, moment),
        SourceReference("source-1", "v1", "/docs/a#b", moment, moment),
        SourceReference("source-1", "v1", "p" * 513, moment, moment),
        SourceReference("source-1", "v1", "/docs/a.md", datetime(2026, 11, 3), moment),
        SourceReference("source-1", "v1", "/docs/a.md", moment, datetime(2026, 11, 4)),
    ]
    for source in invalid:
        with pytest.raises(InputValidationError):
            validate_source_reference(source)


def test_execution_budget_enforces_tool_call_limit() -> None:
    budget = ExecutionBudget(max_steps=2, max_tool_calls=1)
    budget.consume_tool_call()

    with pytest.raises(ExecutionLimitError):
        budget.consume_tool_call()


@pytest.mark.asyncio
async def test_audit_rejects_duplicate_ids_and_sequence_gaps() -> None:
    sink = InMemoryAuditSink(EVENT_SCHEMA)
    await sink.append(make_event())

    with pytest.raises(DuplicateEventError):
        await sink.append(make_event())
    with pytest.raises(EventSequenceError):
        await sink.append(make_event(sequence=2, event_id="event-000002"))


@pytest.mark.asyncio
async def test_audit_rejects_identity_changes_and_payload_attributes() -> None:
    sink = InMemoryAuditSink(EVENT_SCHEMA)
    await sink.append(make_event())

    with pytest.raises(AuditError, match="cannot change identity"):
        await sink.append(
            make_event(
                sequence=1,
                event_id="event-000002",
                context=make_context(tenant="tenant-0002"),
            )
        )

    second_sink = InMemoryAuditSink(EVENT_SCHEMA)
    with pytest.raises(AuditError, match="prompt"):
        await second_sink.append(make_event(attributes={"promptText": "do not record this"}))
