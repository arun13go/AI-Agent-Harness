"""Prove that operational export cannot carry business or caller payloads."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_harness.adapters.foundry.telemetry import (
    MetadataOnlyExporter,
    create_telemetry_provider,
)
from agent_harness.core.errors import ConfigurationError


def test_export_removes_payloads_even_when_instrumentation_adds_them() -> None:
    destination = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name": "secret-resource"}))
    provider.add_span_processor(SimpleSpanProcessor(MetadataOnlyExporter(destination)))
    tracer = provider.get_tracer("secret-scope")
    with tracer.start_as_current_span("secret-prompt") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.usage.input_tokens", 123)
        span.set_attribute("gen_ai.input.messages", "secret-prompt")
        span.set_attribute("http.request.header.authorization", "secret-token")
        span.set_attribute("user.id", "secret-user")
        span.add_event("secret-event", {"payload": "secret-output"})
        span.set_status(trace.Status(trace.StatusCode.ERROR, "secret-error"))
    exported = destination.get_finished_spans()
    assert len(exported) == 1
    safe = exported[0]
    assert safe.name == "harness.chat"
    assert dict(safe.attributes or {}) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.usage.input_tokens": 123,
    }
    assert safe.events == ()
    assert safe.links == ()
    assert safe.status.description is None
    assert safe.status.status_code == trace.StatusCode.ERROR
    assert safe.resource.attributes["service.name"] == "agent-harness"
    assert "secret" not in safe.to_json()
    provider.shutdown()


def test_application_insights_requires_explicit_configuration() -> None:
    with pytest.raises(ConfigurationError, match="connection string"):
        create_telemetry_provider({})


def test_export_failure_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    class FailingExporter(InMemorySpanExporter):
        def export(self, spans: object) -> SpanExportResult:
            del spans
            return SpanExportResult.FAILURE

    result = MetadataOnlyExporter(FailingExporter()).export(())
    assert result == SpanExportResult.FAILURE
    assert "telemetry export failed" in caplog.text
