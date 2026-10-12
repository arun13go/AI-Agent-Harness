# pyright: reportUnknownMemberType=false

"""Store schema-valid audit events in memory for local execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker

from agent_harness.core.errors import (
    AuditError,
    DuplicateEventError,
    EventSequenceError,
)
from agent_harness.core.types import JsonValue, RunEvent


def run_event_to_dict(event: RunEvent) -> dict[str, JsonValue]:
    """Convert a typed event to its versioned schema shape."""

    context = event.context
    value: dict[str, JsonValue] = {
        "schemaVersion": event.schema_version,
        "eventId": event.event_id,
        "eventType": event.event_type,
        "occurredAt": event.occurred_at.isoformat(),
        "sequence": event.sequence,
        "tenantScope": context.identity.tenant_scope,
        "subjectScope": context.identity.subject_scope,
        "runId": context.run_id,
        "correlationId": context.correlation_id,
        "traceId": context.trace_id,
        "source": event.source,
        "outcome": event.outcome,
        "attributes": cast(dict[str, JsonValue], event.attributes),
    }
    return value


class InMemoryAuditSink:
    """Append ordered, correlated events after schema validation."""

    def __init__(self, schema_path: Path) -> None:
        try:
            with schema_path.open(encoding="utf-8") as stream:
                schema = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise AuditError(f"Cannot load run-event schema '{schema_path}': {error}") from error
        if not isinstance(schema, dict):
            raise AuditError(f"Run-event schema '{schema_path}' must contain a JSON object")

        typed_schema = cast(dict[str, JsonValue], schema)
        self._validator = Draft202012Validator(typed_schema, format_checker=FormatChecker())
        self._events: list[RunEvent] = []
        self._event_ids: set[str] = set()
        self._next_sequence: dict[str, int] = {}
        self._run_contexts: dict[str, tuple[str, str, str, str]] = {}

    @property
    def events(self) -> tuple[RunEvent, ...]:
        """Return an immutable snapshot of recorded events."""

        return tuple(self._events)

    async def append(self, event: RunEvent) -> None:
        """Validate and append one event without allowing mutation or gaps."""

        if event.event_id in self._event_ids:
            raise DuplicateEventError(f"Event ID '{event.event_id}' already exists")

        expected_sequence = self._next_sequence.get(event.context.run_id, 0)
        if event.sequence != expected_sequence:
            raise EventSequenceError(
                f"Run '{event.context.run_id}' expected event sequence {expected_sequence}, "
                f"received {event.sequence}"
            )

        run_context = (
            event.context.identity.tenant_scope,
            event.context.identity.subject_scope,
            event.context.correlation_id,
            event.context.trace_id,
        )
        existing_context = self._run_contexts.get(event.context.run_id)
        if existing_context is not None and existing_context != run_context:
            raise AuditError(f"Run '{event.context.run_id}' cannot change identity or correlation")

        validation_errors = sorted(
            self._validator.iter_errors(run_event_to_dict(event)),
            key=lambda error: list(error.absolute_path),
        )
        if validation_errors:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
                f"{error.message}"
                for error in validation_errors
            )
            raise AuditError(f"Run event failed schema validation: {details}")

        self._events.append(event)
        self._event_ids.add(event.event_id)
        self._next_sequence[event.context.run_id] = expected_sequence + 1
        self._run_contexts.setdefault(event.context.run_id, run_context)
