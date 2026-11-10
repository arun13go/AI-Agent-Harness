"""Answer Cosmos DB REST requests from a fake backend, so the REST layer runs without a network."""

from __future__ import annotations

import asyncio
import email.message
import io
import json
import re
import threading
import urllib.error
import urllib.request
from types import TracebackType
from typing import Any

from agent_harness.adapters.storage.cosmos_stores import DocumentConflict
from tests.conformance.fake_documents import FakeDocumentBackend


class Reply:
    """Stand in for a urllib response."""

    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None):
        self.status = status
        self.headers = email.message.Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self._body = io.BytesIO(b"" if body is None else json.dumps(body).encode())

    def __enter__(self) -> Reply:
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


def http_error(request: urllib.request.Request, status: int, **headers: str) -> Exception:
    message = email.message.Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return urllib.error.HTTPError(
        request.full_url, status, "error", message, io.BytesIO(b"secret-document-text")
    )


class FakeCosmosHttp:
    """Serve create, read, replace and query for one container, as the service would."""

    def __init__(self, unique_field: str | None = None) -> None:
        self.documents = FakeDocumentBackend(unique_field)
        self.requests: list[urllib.request.Request] = []
        self._lock = threading.Lock()

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> Reply:
        """Handle one request atomically; the backend runs in a worker thread, like a socket."""

        with self._lock:
            self.requests.append(request)
            return asyncio.run(_serve(self, request))


def _partition(request: urllib.request.Request) -> str:
    (partition,) = json.loads(request.get_header("X-ms-documentdb-partitionkey") or "[]")
    return partition


def _body(request: urllib.request.Request) -> Any:
    assert isinstance(request.data, bytes)
    return json.loads(request.data)


async def _serve(fake: FakeCosmosHttp, request: urllib.request.Request) -> Reply:
    partition = _partition(request)
    tail = request.full_url.rsplit("/docs", 1)[1]
    if request.get_method() == "POST" and request.get_header("X-ms-documentdb-isquery"):
        body = _body(request)
        names = re.findall(r"c\.(\w+) = @p\d", body["query"])
        equals = {n: p["value"] for n, p in zip(names, body["parameters"], strict=True)}
        return Reply(200, {"Documents": await fake.documents.find(partition, **equals)})
    if request.get_method() == "POST":
        try:
            await fake.documents.create(partition, _body(request))
        except DocumentConflict:
            raise http_error(request, 409) from None
        return Reply(201, _body(request))
    document_id = tail.lstrip("/")
    if request.get_method() == "GET":
        found = await fake.documents.read(partition, document_id)
        if found is None:
            raise http_error(request, 404)
        return Reply(200, found)
    try:
        await fake.documents.replace(
            partition, _body(request), etag=request.get_header("If-match") or ""
        )
    except DocumentConflict:
        raise http_error(request, 412) from None
    return Reply(200, _body(request))
