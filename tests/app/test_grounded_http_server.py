"""Prove the grounded HTTP host answers only from the deployment's label scope."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from agent_harness.adapters.maf.agent_executor import MafAgentExecutor
from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.app.agent_http_server import (
    create_grounded_responses_host,
    create_responses_host,
    main,
)
from agent_harness.app.caller_labels import DeploymentCallerLabels
from agent_harness.app.grounded_executor import (
    MAXIMUM_QUESTION_CHARACTERS,
    create_grounded_executor,
)
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.contracts import Tool
from agent_harness.core.errors import ConfigurationError
from agent_harness.core.registry import Registry
from agent_harness.core.types import IdentityScope, RetrievedItem, SourceReference
from tests.adapters.maf.test_agent_executor import ScriptedStreamingClient
from tests.conformance.test_context_policy import RecordingClient

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"
IDENTITY = IdentityScope("tenant-0001", "subject-0001")
OPS = "group:ops"
FINANCE = "group:finance"


def evidence(item_id: str, labels: Sequence[str], *, tenant: str = "tenant-0001") -> RetrievedItem:
    now = datetime.now(UTC)
    return RetrievedItem(
        item_id=item_id,
        content=f"MARKER-{item_id.upper()} restart procedure text.",
        score=0.9,
        source=SourceReference(
            source_id=f"source-{item_id}",
            source_version="v1",
            structural_path=f"/docs/{item_id}.md",
            ingested_at=now - timedelta(days=1),
            fresh_until=now + timedelta(days=30),
        ),
        tenant_scope=tenant,
        authorization_labels=tuple(labels),
        evidence_flags=(),
        source_label="untrusted-retrieved-content",
    )


class Spy:
    """Count retrievals and record the labels each one used."""

    def __init__(self, inner: InMemoryRetrievalProvider) -> None:
        self.labels: list[tuple[str, ...]] = []
        self._inner = inner

    async def retrieve(
        self,
        *,
        identity: IdentityScope,
        authorization_labels: Sequence[str],
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]:
        self.labels.append(tuple(authorization_labels))
        return await self._inner.retrieve(
            identity=identity,
            authorization_labels=authorization_labels,
            query=query,
            limit=limit,
        )


class BrokenResolver:
    async def resolve(self, identity: IdentityScope) -> Sequence[str]:
        del identity
        raise RuntimeError("SECRET directory detail")


def compose(
    items: Sequence[RetrievedItem],
    labels: Sequence[str] | None,
    *,
    client: RecordingClient | ScriptedStreamingClient | None = None,
    resolver: object | None = None,
) -> tuple[httpx.AsyncClient, InMemoryAuditSink, Spy, RecordingClient | ScriptedStreamingClient]:
    config = load_harness_config(ROOT / "config/profiles/grounded-assistant-local.yaml", SCHEMA)
    audit = InMemoryAuditSink(EVENT_SCHEMA)
    model = client or RecordingClient()
    spy = Spy(InMemoryRetrievalProvider(items))
    runtime = MafAgentExecutor(
        config=config,
        client=model,  # type: ignore[arg-type]
        tools=Registry[Tool]("tool"),
        audit_sink=audit,
    )
    executor = create_grounded_executor(
        config=config,
        retrieval=spy,
        runtime=runtime,
        audit_sink=audit,
        label_resolver=resolver or DeploymentCallerLabels(tuple(labels or ())),  # type: ignore[arg-type]
    )

    async def authenticate(request: Request) -> IdentityScope:
        del request
        return IDENTITY

    app = create_grounded_responses_host(
        config=config, executor=executor, authenticate=authenticate
    )
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")
    return http, audit, spy, model


def text_of(body: dict[str, Any]) -> str:
    return "".join(part["text"] for item in body["output"] for part in item["content"])


def sse_events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: {")
    ]


def event_types(audit: InMemoryAuditSink) -> list[str]:
    return [event.event_type for event in audit.events]


@pytest.mark.asyncio
async def test_a_supported_question_is_answered_with_a_deterministic_sources_list() -> None:
    http, audit, _, model = compose([evidence("alpha", (OPS,))], [OPS])

    async with http:
        response = await http.post("/responses", json={"input": "How do I restart?"})

    body = response.json()
    assert response.status_code == 200 and body["status"] == "completed"
    text = text_of(body)
    assert text.startswith("Done.")
    assert body["usage"]["output_tokens"] == 1 and body["usage"]["input_tokens"] == 1
    assert text.endswith("\n\nSources:\n- source-alpha (/docs/alpha.md, version v1)")
    assert event_types(audit) == [
        "run.started",
        "retrieval.completed",
        "model.requested",
        "model.completed",
        "run.completed",
    ]
    assert len(model.requests) == 1  # type: ignore[union-attr]
    assert "MARKER-ALPHA" in json.dumps([m.text for m in model.requests[0]])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_sse_streams_deltas_then_sources_with_one_ordered_event_sequence() -> None:
    http, audit, _, _ = compose(
        [evidence("alpha", (OPS,))], [OPS], client=ScriptedStreamingClient()
    )

    async with http:
        response = await http.post(
            "/responses", json={"input": "How do I restart?", "stream": True}
        )

    events = sse_events(response.text)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert events[0]["type"] == "response.created"
    assert events[-1]["type"] == "response.completed"
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    deltas = [e["delta"] for e in events if e["type"] == "response.output_text.delta"]
    assert deltas[:2] == ["API ", "is healthy."]
    assert deltas[-1] == "\n\nSources:\n- source-alpha (/docs/alpha.md, version v1)"
    assert events[-1]["response"]["usage"]["output_tokens"] == 2  # type: ignore[index]
    assert event_types(audit).count("run.started") == 1
    assert audit.events[-1].event_type == "run.completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_unanswerable_questions_return_fixed_text_without_a_model_call(
    stream: bool,
) -> None:
    http, audit, _, model = compose([], [OPS], client=ScriptedStreamingClient())

    async with http:
        response = await http.post("/responses", json={"input": "Unknown?", "stream": stream})

    assert "I can't answer this from the evidence available to me." in response.text
    assert "Sources" not in response.text
    assert event_types(audit) == ["run.started", "retrieval.completed", "run.completed"]
    assert model.trace_ids == []  # type: ignore[union-attr]
    if not stream:
        assert response.json()["usage"]["output_tokens"] == 0


@pytest.mark.asyncio
async def test_the_same_index_returns_different_evidence_for_different_deployment_labels() -> None:
    items = [evidence("ops-doc", (OPS,)), evidence("finance-doc", (FINANCE,))]
    answers: dict[str, str] = {}
    for name, labels in {"ops": [OPS], "finance": [FINANCE], "none": []}.items():
        http, _, _, _ = compose(items, labels)
        async with http:
            response = await http.post("/responses", json={"input": "How do I restart?"})
        answers[name] = text_of(response.json())

    assert "source-ops-doc" in answers["ops"] and "source-finance-doc" not in answers["ops"]
    assert "source-finance-doc" in answers["finance"] and "source-ops-doc" not in answers["finance"]
    assert "I can't answer" in answers["none"]


@pytest.mark.asyncio
async def test_another_tenants_evidence_is_never_returned() -> None:
    http, _, _, model = compose([evidence("other", (OPS,), tenant="tenant-0002")], [OPS])

    async with http:
        response = await http.post("/responses", json={"input": "How do I restart?"})

    assert "I can't answer" in response.text
    assert "other" not in response.text
    assert model.requests == []  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"input": "hi", "authorization_labels": ["group:finance"]},
        {"input": "hi", "labels": ["group:finance"]},
        {"input": "hi", "metadata": {"authorization_labels": "group:finance"}},
        {"input": "hi", "user": "group:finance"},
        {"input": [{"role": "user", "content": "hi", "authorization_labels": ["group:finance"]}]},
    ],
)
async def test_a_request_cannot_supply_or_widen_caller_labels(payload: dict[str, object]) -> None:
    http, audit, spy, _ = compose([evidence("finance-doc", (FINANCE,))], [OPS])

    async with http:
        response = await http.post("/responses", json=payload)

    assert response.status_code == 400
    assert spy.labels == [] and audit.events == ()


@pytest.mark.asyncio
async def test_a_resolver_failure_closes_the_run_before_any_retrieval_without_leaking() -> None:
    http, audit, spy, model = compose([evidence("alpha", (OPS,))], None, resolver=BrokenResolver())

    async with http:
        response = await http.post("/responses", json={"input": "How do I restart?"})

    body = response.json()
    assert body["status"] == "failed" and body["error"]["code"] == "authorization_error"
    assert "SECRET" not in response.text
    assert event_types(audit) == ["run.started", "run.failed"]
    assert spy.labels == [] and model.requests == []  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_an_overlong_question_fails_validation_before_any_work() -> None:
    http, audit, spy, _ = compose([evidence("alpha", (OPS,))], [OPS])

    async with http:
        response = await http.post(
            "/responses", json={"input": "q" * (MAXIMUM_QUESTION_CHARACTERS + 1)}
        )

    body = response.json()
    assert body["status"] == "failed" and body["error"]["code"] == "input_validation_error"
    assert spy.labels == [] and audit.events == ()


@pytest.mark.asyncio
async def test_earlier_user_messages_are_history_and_the_last_is_the_question() -> None:
    http, _, spy, model = compose([evidence("alpha", (OPS,))], [OPS])

    async with http:
        response = await http.post(
            "/responses",
            json={
                "input": [
                    {"role": "user", "content": "EARLIER-MARKER"},
                    {"role": "user", "content": "How do I restart?"},
                ]
            },
        )

    assert response.json()["status"] == "completed"
    sent = [m.text for m in model.requests[0]]  # type: ignore[union-attr]
    assert "EARLIER-MARKER" in sent[0] and "How do I restart?" in sent[-1]
    assert len(spy.labels) == 1


@pytest.mark.asyncio
async def test_audit_records_only_the_label_count() -> None:
    http, audit, _, _ = compose([evidence("alpha", (OPS,))], [OPS])

    async with http:
        await http.post("/responses", json={"input": "How do I restart? QUESTION-MARKER"})

    dump = json.dumps([dict(event.attributes) for event in audit.events])
    assert (
        next(e for e in audit.events if e.event_type == "retrieval.completed").attributes[
            "callerLabelCount"
        ]
        == 1
    )
    for forbidden in (OPS, "QUESTION-MARKER", "MARKER-ALPHA"):
        assert forbidden not in dump


def test_hosts_refuse_the_wrong_profile() -> None:
    assistant = load_harness_config(ROOT / "config/profiles/assistant.yaml", SCHEMA)
    grounded = load_harness_config(ROOT / "config/profiles/grounded-assistant-local.yaml", SCHEMA)
    audit = InMemoryAuditSink(EVENT_SCHEMA)
    runtime = MafAgentExecutor(
        config=grounded,
        client=RecordingClient(),  # type: ignore[arg-type]
        tools=Registry[Tool]("tool"),
        audit_sink=audit,
    )
    executor = create_grounded_executor(
        config=grounded,
        retrieval=InMemoryRetrievalProvider([]),
        runtime=runtime,
        audit_sink=audit,
        label_resolver=DeploymentCallerLabels(()),
    )

    async def authenticate(request: Request) -> IdentityScope:
        del request
        return IDENTITY

    with pytest.raises(ConfigurationError, match="grounded-assistant profile"):
        create_grounded_responses_host(
            config=assistant, executor=executor, authenticate=authenticate
        )
    with pytest.raises(ConfigurationError, match="assistant profile"):
        create_responses_host(
            config=grounded,
            client=RecordingClient(),  # type: ignore[arg-type]
            tools=Registry[Tool]("tool"),
            audit_sink=audit,
            authenticate=authenticate,
        )


AZURE_PROFILE = "config/profiles/grounded-assistant.yaml"
LOCAL_PROFILE = "config/profiles/grounded-assistant-local.yaml"
LOCAL_IDENTITY = ["--tenant", "local-tenant", "--subject", "local-user"]


def run_main(monkeypatch: pytest.MonkeyPatch, *arguments: str, labels: str | None = None) -> None:
    monkeypatch.setattr("sys.argv", ["responses-host", *arguments])
    if labels is None:
        monkeypatch.delenv("HARNESS_CALLER_LABELS", raising=False)
    else:
        monkeypatch.setenv("HARNESS_CALLER_LABELS", labels)
    monkeypatch.delenv("FOUNDRY_HOSTING_ENVIRONMENT", raising=False)
    main()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--profile", LOCAL_PROFILE, "--hosted", "--corpus", "evals"],
        ["--profile", AZURE_PROFILE, *LOCAL_IDENTITY, "--corpus", "evals"],
        ["--profile", AZURE_PROFILE, *LOCAL_IDENTITY, "--corpus-label", "group:ops"],
        ["--profile", LOCAL_PROFILE, *LOCAL_IDENTITY],
        ["--profile", "config/profiles/assistant.yaml", *LOCAL_IDENTITY, "--corpus", "evals"],
    ],
)
def test_grounded_startup_rejects_inconsistent_corpus_arguments(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        run_main(monkeypatch, *arguments, labels="[]")

    assert error.value.code == 2


@pytest.mark.parametrize("labels", [None, "", "not json", '["a","a"]', '["bad label"]'])
def test_grounded_startup_fails_closed_on_missing_or_malformed_caller_labels(
    monkeypatch: pytest.MonkeyPatch, labels: str | None
) -> None:
    with pytest.raises(ConfigurationError, match="HARNESS_CALLER_LABELS"):
        run_main(monkeypatch, "--profile", AZURE_PROFILE, *LOCAL_IDENTITY, labels=labels)
