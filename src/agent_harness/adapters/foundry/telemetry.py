"""Export only allowlisted operational span metadata to Application Insights."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanContext, Status

from agent_harness.core.errors import ConfigurationError

_LOGGER = logging.getLogger(__name__)
_RESOURCE = Resource({"service.name": "agent-harness", "service.version": "0.1.0"})
_COUNTS = frozenset(
    {
        "http.response.status_code",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.cache_read.input_tokens",
    }
)
_OPERATIONS = frozenset({"invoke_agent", "chat", "execute_tool", "generate_content"})


def _context(value: SpanContext | None) -> SpanContext | None:
    if value is None:
        return None
    return SpanContext(
        trace_id=value.trace_id,
        span_id=value.span_id,
        is_remote=value.is_remote,
        trace_flags=value.trace_flags,
    )


class MetadataOnlyExporter(SpanExporter):
    """Drop payloads, events, links, dynamic names, resources, and status descriptions."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        sanitized: list[ReadableSpan] = []
        for span in spans:
            attributes: dict[str, str | int | float] = {}
            for key, value in (span.attributes or {}).items():
                if (
                    key in _COUNTS
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ):
                    attributes[key] = value
                elif key == "gen_ai.operation.name" and isinstance(value, str):
                    if value in _OPERATIONS:
                        attributes[key] = value
            operation = attributes.get("gen_ai.operation.name")
            name = (
                "harness.http"
                if span.name == "harness.http"
                else f"harness.{operation}"
                if operation
                else "harness.operation"
            )
            sanitized.append(
                ReadableSpan(
                    name=name,
                    context=_context(span.context),
                    parent=_context(span.parent),
                    resource=_RESOURCE,
                    attributes=attributes,
                    kind=span.kind,
                    status=Status(span.status.status_code),
                    start_time=span.start_time,
                    end_time=span.end_time,
                )
            )
        result = self._exporter.export(sanitized)
        if result is not SpanExportResult.SUCCESS:
            _LOGGER.error("Operational telemetry export failed")
        return result

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._exporter.force_flush(timeout_millis)


def create_telemetry_provider(environment: Mapping[str, str]) -> TracerProvider:
    """Create an explicit trace-only exporter; never enable automatic payload logging."""

    connection = environment.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "").strip()
    if not connection:
        raise ConfigurationError("Application Insights requires a connection string")
    exporter = AzureMonitorTraceExporter(
        connection_string=connection,
        disable_offline_storage=True,
    )
    provider = TracerProvider(resource=_RESOURCE)
    provider.add_span_processor(BatchSpanProcessor(MetadataOnlyExporter(exporter)))
    return provider
