"""Prove the REST backend validates its endpoint, bounds its I/O, and leaks nothing in errors."""

from __future__ import annotations

import asyncio
import email.message
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from types import TracebackType
from typing import Any

import pytest

from agent_harness.adapters.http_support import NoRedirect
from agent_harness.adapters.retrieval.azure_search import (
    CORPUS_FIELD,
    INDEX_FIELDS,
    LABELS_FIELD,
    SCORE_KEY,
    TENANT_FIELD,
    AzureSearchRetrievalProvider,
    evidence_to_document,
    linear_score,
    saturating_score,
)
from agent_harness.adapters.retrieval.azure_search_rest import (
    API_VERSION,
    RERANKER_KEY,
    SEMANTIC_CONFIGURATION,
    TOKEN_SCOPE,
    AzureSearchRestBackend,
    index_definition,
    validate_endpoint,
)
from agent_harness.core.errors import InputValidationError, RetrievalError
from tests.conformance.fixtures import IDENTITY_A, LABELS, NOW, make_evidence

ENDPOINT = "https://srch-test.search.windows.net"


class Credential:
    def __init__(self, expires_on: int = 4_102_444_800) -> None:
        self.scopes: list[tuple[str, ...]] = []
        self._expires_on = expires_on

    def get_token(self, *scopes: str) -> Any:
        self.scopes.append(scopes)
        return type("Token", (), {"token": "fake-token-value", "expires_on": self._expires_on})()


class Response:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def __enter__(self) -> Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class FakeOpener:
    """Stand in for the URL opener and record what would have been sent."""

    def __init__(self, handler: Callable[[urllib.request.Request], bytes]) -> None:
        self.handler = handler
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> Response:
        self.requests.append(request)
        return Response(self.handler(request))


def backend(
    handler: Callable[[urllib.request.Request], bytes],
    *,
    credential: Credential | None = None,
    index: str = "harness-chunks",
) -> tuple[AzureSearchRestBackend, FakeOpener]:
    opener = FakeOpener(handler)
    return (
        AzureSearchRestBackend(
            endpoint=ENDPOINT,
            index_name=index,
            credential=credential or Credential(),
            opener=opener,  # pyright: ignore[reportArgumentType]
        ),
        opener,
    )


def body_of(request: urllib.request.Request) -> dict[str, Any]:
    assert isinstance(request.data, bytes)
    return json.loads(request.data)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://srch-test.search.windows.net",
        "https://srch-test.search.windows.net/",
        "https://SRCH-TEST.search.windows.net:443",
        "https://srch-test.search.azure.us",
    ],
)
def test_valid_endpoints_are_canonicalized(endpoint: str) -> None:
    assert validate_endpoint(endpoint).startswith("https://srch-test.search.")
    assert validate_endpoint(endpoint).islower()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://srch-test.search.windows.net",
        "https://srch-test.example.com",
        "https://srch-test.search.windows.net.evil.example",
        "https://evil.example/.search.windows.net",
        "https://user@srch-test.search.windows.net",
        "https://user:pw@srch-test.search.windows.net",
        "https://srch-test.search.windows.net:8443",
        "https://srch-test.search.windows.net/indexes",
        "https://srch-test.search.windows.net/?x=1",
        "https://srch-test.search.windows.net/#x",
        "https://.search.windows.net",
        "https://-bad.search.windows.net",
        "https://a.b.search.windows.net",
        "https://localhost",
        "",
        "srch-test.search.windows.net",
    ],
)
def test_endpoints_that_could_receive_a_token_elsewhere_are_rejected(endpoint: str) -> None:
    with pytest.raises(InputValidationError):
        validate_endpoint(endpoint)


def test_a_bad_endpoint_or_index_name_never_requests_a_token() -> None:
    credential = Credential()
    for endpoint, index in [
        ("https://evil.example", "ok"),
        (ENDPOINT, "../other"),
        (ENDPOINT, "A"),
    ]:
        with pytest.raises(InputValidationError):
            AzureSearchRestBackend(endpoint=endpoint, index_name=index, credential=credential)
    assert credential.scopes == []


@pytest.mark.parametrize("timeout", [0.0, -1.0, 121.0])
def test_timeout_must_be_bounded(timeout: float) -> None:
    with pytest.raises(InputValidationError):
        AzureSearchRestBackend(
            endpoint=ENDPOINT, index_name="ok", credential=Credential(), timeout_seconds=timeout
        )


@pytest.mark.asyncio
async def test_search_sends_scoped_request_with_a_search_scope_token() -> None:
    credential = Credential()
    service, opener = backend(lambda _: b'{"value": []}', credential=credential)

    result = await service.search(text="restart", odata_filter="tenantScope eq 'x'", top=5)

    [request] = opener.requests
    assert result == []
    assert request.full_url == (
        f"{ENDPOINT}/indexes/harness-chunks/docs/search?api-version={API_VERSION}"
    )
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer fake-token-value"
    assert credential.scopes == [(TOKEN_SCOPE,)]
    sent = body_of(request)
    assert sent["search"] == "restart"
    assert sent["filter"] == "tenantScope eq 'x'"
    assert sent["top"] == 5
    assert sent["queryType"] == "simple"
    assert set(sent["select"].split(",")) == {field["name"] for field in INDEX_FIELDS}


@pytest.mark.asyncio
async def test_token_is_reused_until_shortly_before_it_expires() -> None:
    now = [1_000.0]
    credential = Credential(expires_on=2_000)
    opener = FakeOpener(lambda _: b'{"value": []}')
    service = AzureSearchRestBackend(
        endpoint=ENDPOINT,
        index_name="ok",
        credential=credential,
        opener=opener,  # pyright: ignore[reportArgumentType]
        clock=lambda: now[0],
    )

    for _ in range(3):
        await service.search(text="x", odata_filter="f", top=1)
    assert len(credential.scopes) == 1

    now[0] = 1_699.0  # 301 seconds left: still outside the refresh window
    await service.search(text="x", odata_filter="f", top=1)
    assert len(credential.scopes) == 1

    now[0] = 1_701.0  # 299 seconds left: refresh
    await service.search(text="x", odata_filter="f", top=1)
    assert len(credential.scopes) == 2


@pytest.mark.asyncio
async def test_provider_works_end_to_end_over_the_rest_backend() -> None:
    stored = evidence_to_document(make_evidence("chunk-a", content="Restart the service."))

    def handler(request: urllib.request.Request) -> bytes:
        assert "tenantScope eq 'tenant-0001'" in body_of(request)["filter"]
        return json.dumps({"value": [{**stored, SCORE_KEY: 3.0}]}).encode()

    service, _ = backend(handler)
    provider = AzureSearchRetrievalProvider(
        service, score_mapper=saturating_score(1.0), clock=lambda: NOW
    )

    [found] = await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart service", limit=5
    )

    assert found.item_id == "chunk-a"
    assert found.score == 0.75


@pytest.mark.asyncio
async def test_http_errors_report_the_status_without_the_response_body() -> None:
    def handler(request: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            email.message.Message(),
            io.BytesIO(b"secret-document-text"),
        )

    service, _ = backend(handler)

    with pytest.raises(RetrievalError) as raised:
        await service.search(text="x", odata_filter="f", top=1)

    assert "429" in str(raised.value)
    assert "secret-document-text" not in str(raised.value)
    assert "fake-token-value" not in str(raised.value)


@pytest.mark.asyncio
async def test_transport_and_credential_failures_become_retrieval_errors() -> None:
    def broken(_: urllib.request.Request) -> bytes:
        raise TimeoutError("host secret-host.example")

    class FailingCredential(Credential):
        def get_token(self, *scopes: str) -> Any:
            raise RuntimeError("tenant-secret-id")

    service, _ = backend(broken)
    with pytest.raises(RetrievalError) as first:
        await service.search(text="x", odata_filter="f", top=1)
    failing, _ = backend(lambda _: b"{}", credential=FailingCredential())
    with pytest.raises(RetrievalError) as second:
        await failing.search(text="x", odata_filter="f", top=1)

    assert "secret-host" not in str(first.value)
    assert "tenant-secret-id" not in str(second.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"not json", b"[]", b'{"value": "x"}', b'{"value": [1]}', b'{"other": []}'],
)
async def test_unexpected_responses_are_rejected(payload: bytes) -> None:
    service, _ = backend(lambda _: payload)

    with pytest.raises(RetrievalError):
        await service.search(text="x", odata_filter="f", top=1)


@pytest.mark.asyncio
async def test_oversized_responses_are_rejected_without_reading_them_all() -> None:
    reads: list[int] = []

    class Counting(Response):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return super().read(size)

    class Opener(FakeOpener):
        def open(self, request: urllib.request.Request, timeout: float | None = None) -> Response:
            return Counting(b"x" * 9_000_000)

    service = AzureSearchRestBackend(
        endpoint=ENDPOINT,
        index_name="ok",
        credential=Credential(),
        opener=Opener(lambda _: b""),  # pyright: ignore[reportArgumentType]
    )

    with pytest.raises(RetrievalError, match="too large"):
        await service.search(text="x", odata_filter="f", top=1)

    assert reads == [8 * 1024 * 1024 + 1]


def test_redirects_are_never_followed() -> None:
    handler = NoRedirect()
    request = urllib.request.Request(ENDPOINT)

    assert handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example") is None


@pytest.mark.asyncio
async def test_upload_batches_and_requires_every_document_to_succeed() -> None:
    calls: list[int] = []

    def handler(request: urllib.request.Request) -> bytes:
        batch = body_of(request)["value"]
        calls.append(len(batch))
        assert all(entry["@search.action"] == "mergeOrUpload" for entry in batch)
        return json.dumps(
            {"value": [{"key": str(i), "status": True} for i in range(len(batch))]}
        ).encode()

    service, _ = backend(handler)

    assert await service.upload([{"id": str(i)} for i in range(1001)]) == 1001
    assert calls == [500, 500, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {"value": [{"key": "0", "status": False}]},
        {"value": []},
        {"value": [{"key": "0"}]},
        {},
    ],
)
async def test_failed_or_missing_upload_results_are_rejected(reply: dict[str, Any]) -> None:
    service, _ = backend(lambda _: json.dumps(reply).encode())

    with pytest.raises(RetrievalError):
        await service.upload([{"id": "0"}])


@pytest.mark.asyncio
async def test_create_and_delete_index_use_the_expected_verbs() -> None:
    service, opener = backend(lambda _: b"")

    await service.create_index()
    await service.delete_index()

    assert [request.get_method() for request in opener.requests] == ["PUT", "DELETE"]
    assert body_of(opener.requests[0])["name"] == "harness-chunks"


def test_index_definition_states_every_attribute_and_limits_filtering_to_scope() -> None:
    definition = index_definition("harness-chunks")
    fields = {field["name"]: field for field in definition["fields"]}

    assert set(fields) == {field["name"] for field in INDEX_FIELDS}
    assert [name for name, field in fields.items() if field["key"]] == ["id"]
    assert {name for name, field in fields.items() if field["filterable"]} == {
        "id",
        TENANT_FIELD,
        LABELS_FIELD,
        CORPUS_FIELD,
    }
    assert [name for name, field in fields.items() if field["searchable"]] == ["content"]
    assert [name for name, field in fields.items() if field["sortable"]] == ["id"]
    assert all(not field["facetable"] for field in fields.values())
    assert all(field["retrievable"] for field in fields.values())


def test_upload_and_search_use_documents_the_provider_reads() -> None:
    document = evidence_to_document(make_evidence("chunk-a"))

    assert set(document) == {field["name"] for field in INDEX_FIELDS}


def test_index_definition_declares_the_semantic_configuration_over_content_only() -> None:
    [configuration] = index_definition("harness-chunks")["semantic"]["configurations"]

    assert configuration["name"] == SEMANTIC_CONFIGURATION
    assert configuration["prioritizedFields"] == {
        "prioritizedContentFields": [{"fieldName": "content"}]
    }


def semantic_backend(
    hits: list[dict[str, Any]],
) -> tuple[AzureSearchRestBackend, FakeOpener]:
    opener = FakeOpener(lambda _: json.dumps({"value": hits}).encode())
    return (
        AzureSearchRestBackend(
            endpoint=ENDPOINT,
            index_name="harness-chunks",
            credential=Credential(),
            opener=opener,  # pyright: ignore[reportArgumentType]
            semantic=True,
        ),
        opener,
    )


@pytest.mark.asyncio
async def test_semantic_mode_asks_for_reranking_and_reports_the_reranker_score() -> None:
    stored = evidence_to_document(make_evidence("chunk-a"))
    service, opener = semantic_backend(
        [
            {**stored, SCORE_KEY: 9.0, RERANKER_KEY: 3.25},
            {**stored, "id": "chunk-b", SCORE_KEY: 7.0, RERANKER_KEY: None},
            {**stored, "id": "chunk-c", SCORE_KEY: 5.0},
        ]
    )

    hits = await service.search(text="restart", odata_filter="f", top=10)

    sent = body_of(opener.requests[0])
    assert sent["queryType"] == "semantic"
    assert sent["semanticConfiguration"] == SEMANTIC_CONFIGURATION
    assert [hit[SCORE_KEY] for hit in hits] == [3.25, 0.0, 0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("reranker", [-0.5, 4.5, "3", True, float("nan")])
async def test_semantic_mode_rejects_invalid_reranker_scores(reranker: Any) -> None:
    stored = evidence_to_document(make_evidence("chunk-a"))
    service, _ = semantic_backend([{**stored, SCORE_KEY: 1.0, RERANKER_KEY: reranker}])

    with pytest.raises(RetrievalError):
        await service.search(text="restart", odata_filter="f", top=10)


@pytest.mark.asyncio
async def test_unranked_results_are_dropped_by_the_provider() -> None:
    stored = evidence_to_document(make_evidence("chunk-a", content="Restart the service."))
    service, _ = semantic_backend(
        [
            {**stored, SCORE_KEY: 9.0, RERANKER_KEY: 2.0},
            {**stored, "id": "chunk-b", SCORE_KEY: 8.0},
        ]
    )
    provider = AzureSearchRetrievalProvider(
        service, score_mapper=linear_score(4.0), clock=lambda: NOW
    )

    found = await provider.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=5
    )

    assert [(entry.item_id, entry.score) for entry in found] == [("chunk-a", 0.5)]


def test_keyword_mode_is_the_default() -> None:
    service, opener = backend(lambda _: b'{"value": []}')

    asyncio.run(service.search(text="x", odata_filter="f", top=1))

    assert body_of(opener.requests[0])["queryType"] == "simple"
    assert "semanticConfiguration" not in body_of(opener.requests[0])
