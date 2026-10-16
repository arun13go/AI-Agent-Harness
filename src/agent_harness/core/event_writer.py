"""Write ordered run events through the shared audit contract."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from agent_harness.core.contracts import AuditSink
from agent_harness.core.types import JsonScalar, RequestContext, RunEvent


class RunEventWriter:
    """Create correlated events while continuing the run sequence."""

    def __init__(
        self,
        *,
        context: RequestContext,
        audit_sink: AuditSink,
        clock: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._context = context
        self._audit_sink = audit_sink
        self._clock = clock or (lambda: datetime.now(UTC))
        self._event_id_factory = event_id_factory or (lambda: uuid4().hex)

    async def emit(
        self,
        event_type: str,
        *,
        source: str,
        outcome: str,
        attributes: dict[str, JsonScalar | list[JsonScalar]],
    ) -> None:
        """Append one schema-shaped event at the next run sequence."""

        await self._audit_sink.append_next(
            RunEvent(
                schema_version=1,
                event_id=self._event_id_factory(),
                event_type=event_type,
                occurred_at=self._clock(),
                sequence=0,
                context=self._context,
                source=source,
                outcome=outcome,
                attributes=attributes,
            )
        )
