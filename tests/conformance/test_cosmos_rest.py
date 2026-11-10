"""Check the Cosmos REST backend: request shape, status mapping, retries and bounded responses."""

from __future__ import annotations

import io
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from agent_harness.adapters.http_support import NoRedirect
from agent_harness.adapters.storage.cosmos_rest import CosmosRestBackend, validate_endpoint
from agent_harness.adapters.storage.cosmos_stores import DocumentConflict
from agent_harness.core.errors import InputValidationError, StorageError
from tests.conformance.fake_cosmos_http import FakeCosmosHttp, Reply, http_error
from tests.conformance.test_azure_search_rest import Credential

ENDPOINT = "https://acct-test.documents.azure.com"
ID = "a" * 64


def ok() -> Reply:
    return Reply(200, {"id": ID, "_etag": "1"})


class Scripted:
    """Replay one outcome per request: return a reply or raise an error."""

    def __init__(self, *steps: Reply | Exception) -> None:
        self._steps = list(steps)
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> Reply:
        self.requests.append(request)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make(
    opener: Any,
    *,
    credential: Credential | None = None,
    sleeps: list[float] | None = None,
) -> CosmosRestBackend:
    async def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return CosmosRestBackend(
        endpoint=ENDPOINT,
        database="harness",
        container="records",
        credential=credential or Credential(),
        opener=opener,
        sleep=sleep,
        monotonic=lambda: 0.0,
    )


def request_of(request: urllib.request.Request) -> Callable[[str], str | None]:
    return lambda name: request.get_header(name.capitalize())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://acct-test.documents.azure.com",
        "https://ACCT-test.documents.azure.com/",
        "https://acct-test.documents.azure.us:443",
    ],
)
def test_account_endpoints_are_canonicalized(endpoint: str) -> None:
    assert validate_endpoint(endpoint).startswith("https://acct-test.documents.azure.")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://acct-test.documents.azure.com",
        "https://acct-test.documents.azure.com.evil.example",
        "https://evil.example/acct-test.documents.azure.com",
        "https://user:pw@acct-test.documents.azure.com",
        "https://acct-test.documents.azure.com:444",
        "https://acct-test.documents.azure.com/dbs",
        "https://acct-test.documents.azure.com?x=1",
        "https://-bad.documents.azure.com",
        "https://documents.azure.com",
        "",
    ],
)
def test_endpoints_that_could_receive_a_token_elsewhere_are_rejected(endpoint: str) -> None:
    with pytest.raises(InputValidationError):
        validate_endpoint(endpoint)


@pytest.mark.parametrize(
    ("database", "container", "timeout"),
    [("", "c", 15.0), ("d", "a/b", 15.0), ("d", "c", 0.0), ("d", "c", 121.0), ("d d", "c", 15.0)],
)
def test_bad_names_and_timeouts_never_build_a_client(
    database: str, container: str, timeout: float
) -> None:
    with pytest.raises(InputValidationError):
        CosmosRestBackend(
            endpoint=ENDPOINT,
            database=database,
            container=container,
            credential=Credential(),
            timeout_seconds=timeout,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "../x", "A" * 64, "a" * 63, "a" * 65, "a/" + "b" * 62])
async def test_a_bad_document_id_never_reaches_the_network_or_asks_for_a_token(bad: str) -> None:
    credential, opener = Credential(), Scripted()
    backend = make(opener, credential=credential)

    with pytest.raises(InputValidationError):
        await backend.read("tenant-0001", bad)
    with pytest.raises(InputValidationError):
        await backend.replace("tenant-0001", {"id": bad}, etag="1")

    assert opener.requests == [] and credential.scopes == []


@pytest.mark.asyncio
async def test_requests_carry_an_entra_token_the_partition_and_the_etag() -> None:
    credential, http = Credential(), FakeCosmosHttp()
    backend = make(http, credential=credential)

    await backend.create("tenant-0001", {"id": ID, "tenantScope": "tenant-0001"})
    document = await backend.read("tenant-0001", ID)
    assert document is not None
    await backend.replace("tenant-0001", {"id": ID, "tenantScope": "tenant-0001"}, etag="1")

    create, read, replace = (request_of(r) for r in http.requests)
    assert [r.get_method() for r in http.requests] == ["POST", "GET", "PUT"]
    assert http.requests[0].full_url == f"{ENDPOINT}/dbs/harness/colls/records/docs"
    assert http.requests[1].full_url.endswith(f"/docs/{ID}")
    for header in (create, read, replace):
        assert header("Authorization") == "type%3Daad%26ver%3D1.0%26sig%3Dfake-token-value"
        assert header("X-ms-version") == "2018-12-31"
        assert header("X-ms-documentdb-partitionkey") == '["tenant-0001"]'
        assert (header("X-ms-date") or "").endswith("GMT")
    assert replace("If-match") == "1" and create("If-match") is None
    assert credential.scopes == [(f"{ENDPOINT}/.default",)]


@pytest.mark.asyncio
async def test_statuses_map_to_conflicts_and_absence_without_leaking_bodies() -> None:
    request = urllib.request.Request(ENDPOINT)
    backend = make(
        Scripted(
            http_error(request, 409),
            http_error(request, 404),
            http_error(request, 404),
            http_error(request, 412),
            http_error(request, 403),
        )
    )

    with pytest.raises(DocumentConflict):
        await backend.create("t", {"id": ID})
    assert await backend.read("t", ID) is None
    with pytest.raises(DocumentConflict):
        await backend.replace("t", {"id": ID}, etag="1")
    with pytest.raises(DocumentConflict):
        await backend.replace("t", {"id": ID}, etag="1")
    with pytest.raises(StorageError) as raised:
        await backend.read("t", ID)

    assert str(raised.value) == "Cosmos DB returned HTTP 403"


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [Reply(200, {"id": ID}), Reply(204)])
async def test_an_unexpected_success_status_is_an_error_not_a_silent_create(reply: Reply) -> None:
    with pytest.raises(StorageError, match=f"HTTP {reply.status}"):
        await make(Scripted(reply)).create("t", {"id": ID})


@pytest.mark.asyncio
async def test_query_pages_are_followed_and_filters_are_parameterized() -> None:
    first = Reply(200, {"Documents": [{"id": "1"}]}, {"x-ms-continuation": "next"})
    second = Reply(200, {"Documents": [{"id": "2"}]})
    opener = Scripted(first, second)

    found = await make(opener).find("tenant-0001", kind="audit", runId="run'1 OR 1=1")

    assert [d["id"] for d in found] == ["1", "2"]
    request = opener.requests[0]
    assert request.get_header("X-ms-documentdb-isquery") == "True"
    assert request.get_header("Content-type") == "application/query+json"
    assert opener.requests[1].get_header("X-ms-continuation") == "next"
    assert isinstance(request.data, bytes)
    body = request.data.decode()
    assert "c.kind = @p0 AND c.runId = @p1" in body and "OR 1=1" in body
    assert "WHERE c.kind = 'audit'" not in body


@pytest.mark.asyncio
async def test_query_field_names_and_page_count_are_bounded() -> None:
    with pytest.raises(InputValidationError):
        await make(Scripted()).find("t", **{"kind = 1 OR c.x": "v"})
    endless = Scripted(
        *(Reply(200, {"Documents": []}, {"x-ms-continuation": "n"}) for _ in range(10))
    )

    with pytest.raises(StorageError, match="too many pages"):
        await make(endless).find("t", kind="audit")

    assert len(endless.requests) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"Documents": "x"}, {"other": []}, [], {"Documents": [1]}])
async def test_an_unexpected_query_response_is_rejected(body: Any) -> None:
    with pytest.raises(StorageError, match="unexpected response"):
        await make(Scripted(Reply(200, body))).find("t", kind="audit")


@pytest.mark.asyncio
async def test_throttling_is_retried_for_reads_and_writes_honoring_the_service_delay() -> None:
    sleeps: list[float] = []
    request = urllib.request.Request(ENDPOINT)
    slow = http_error(request, 429, x_ms_retry_after_ms="250")
    backend = make(Scripted(slow, ok(), slow, Reply(201)), sleeps=sleeps)

    assert await backend.read("t", ID) is not None
    await backend.create("t", {"id": ID})

    assert sleeps == [0.25, 0.25]


@pytest.mark.asyncio
async def test_a_rejected_token_is_refreshed_once() -> None:
    credential = Credential()
    request = urllib.request.Request(ENDPOINT)
    backend = make(
        Scripted(
            http_error(request, 401), ok(), http_error(request, 401), http_error(request, 401)
        ),
        credential=credential,
    )

    assert await backend.read("t", ID) is not None
    assert len(credential.scopes) == 2
    with pytest.raises(StorageError, match="HTTP 401"):
        await backend.read("t", ID)


@pytest.mark.asyncio
async def test_network_failures_retry_queries_too() -> None:
    opener = Scripted(TimeoutError("down"), Reply(200, {"Documents": []}))

    assert await make(opener).find("t", kind="audit") == []
    assert len(opener.requests) == 2


@pytest.mark.asyncio
async def test_network_failures_retry_reads_but_never_writes() -> None:
    down = TimeoutError("secret-host.example")
    reader = Scripted(down, ok())
    assert await make(reader).read("t", ID) is not None
    assert len(reader.requests) == 2

    async def create(backend: CosmosRestBackend) -> None:
        await backend.create("t", {"id": ID})

    async def replace(backend: CosmosRestBackend) -> None:
        await backend.replace("t", {"id": ID}, etag="1")

    for write in (create, replace):
        writer = Scripted(down, Reply(201))
        with pytest.raises(StorageError) as raised:
            await write(make(writer))
        assert len(writer.requests) == 1 and "secret-host" not in str(raised.value)


@pytest.mark.asyncio
async def test_credential_failures_are_not_retried_and_hide_details() -> None:
    class Failing(Credential):
        def get_token(self, *scopes: str) -> Any:
            self.scopes.append(scopes)
            raise RuntimeError("tenant-secret-id")

    credential = Failing()
    with pytest.raises(StorageError) as raised:
        await make(Scripted(ok()), credential=credential).read("t", ID)

    assert len(credential.scopes) == 1 and "tenant-secret-id" not in str(raised.value)


class Raw(Reply):
    """A reply with an arbitrary body."""

    def __init__(self, raw: bytes) -> None:
        super().__init__(200)
        self._body = io.BytesIO(raw)


@pytest.mark.asyncio
async def test_invalid_oversized_and_non_object_responses_are_rejected() -> None:
    with pytest.raises(StorageError, match="invalid JSON"):
        await make(Scripted(Raw(b"not json"))).read("t", ID)
    with pytest.raises(StorageError, match="unexpected response"):
        await make(Scripted(Raw(b"[1]"))).read("t", ID)
    with pytest.raises(StorageError, match="too large"):
        await make(Scripted(Raw(b"x" * (8 * 1024 * 1024 + 1)))).read("t", ID)


def test_the_default_opener_never_follows_redirects() -> None:
    backend = CosmosRestBackend(
        endpoint=ENDPOINT, database="harness", container="records", credential=Credential()
    )
    handlers: list[object] = getattr(getattr(backend, "_opener"), "handlers")  # noqa: B009

    assert any(isinstance(handler, NoRedirect) for handler in handlers)
