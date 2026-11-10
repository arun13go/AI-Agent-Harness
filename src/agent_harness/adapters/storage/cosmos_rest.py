"""Reach Azure Cosmos DB for NoSQL over REST with Entra tokens; implements ``DocumentBackend``.

The released SDK cannot be installed from every build machine, and four operations need little
code. Shared keys are never used. Writes are not retried after a network failure, because a
retried create or replace that had already succeeded would look like a conflict.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from email.utils import formatdate
from typing import Any, cast
from urllib.parse import quote, urlsplit

from agent_harness.adapters.http_support import (
    BearerTokenCache,
    NoRedirect,
    TokenCredential,
    Transient,
    transient_for,
    with_retries,
)
from agent_harness.adapters.storage.cosmos_stores import Document, DocumentConflict
from agent_harness.core.errors import InputValidationError, StorageError

API_VERSION = "2018-12-31"
_HOST_SUFFIXES = (".documents.azure.com", ".documents.azure.us", ".documents.azure.cn")
_ACCOUNT = re.compile(r"^[a-z0-9][a-z0-9-]{1,42}[a-z0-9]$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
_DOCUMENT_ID = re.compile(r"^[0-9a-f]{64}$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PAGE_SIZE = 100
_MAX_PAGES = 10


def validate_endpoint(endpoint: str) -> str:
    """Return the canonical account URL, or reject anything that is not a Cosmos DB host."""

    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    suffix = next((item for item in _HOST_SUFFIXES if host.endswith(item)), None)
    account = host[: -len(suffix)] if suffix else ""
    if (
        parts.scheme != "https"
        or suffix is None
        or not _ACCOUNT.fullmatch(account)
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise InputValidationError("Cosmos endpoint must be an https Azure Cosmos DB account URL")
    return f"https://{host}"


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: Any
    headers: Mapping[str, str]


class CosmosRestBackend:
    """One container of one database, addressed by tenant partition."""

    def __init__(
        self,
        *,
        endpoint: str,
        database: str,
        container: str,
        credential: TokenCredential,
        timeout_seconds: float = 15.0,
        opener: urllib.request.OpenerDirector | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 < timeout_seconds <= 120.0:
            raise InputValidationError("Cosmos timeout must be between 0 and 120 seconds")
        if not _NAME.fullmatch(database) or not _NAME.fullmatch(container):
            raise InputValidationError("Cosmos database and container names are invalid")
        self._endpoint = validate_endpoint(endpoint)
        self._documents = f"/dbs/{database}/colls/{container}/docs"
        self._timeout = timeout_seconds
        self._opener = opener or urllib.request.build_opener(NoRedirect)
        self._tokens = BearerTokenCache(credential, f"{self._endpoint}/.default", clock=clock)
        self._sleep = sleep
        self._monotonic = monotonic

    async def create(self, partition: str, document: Document) -> None:
        reply = await self._call("POST", "", partition, body=document, expected={201, 409})
        if reply.status == 409:
            raise DocumentConflict

    async def read(self, partition: str, document_id: str) -> Document | None:
        reply = await self._call(
            "GET", f"/{_valid_id(document_id)}", partition, expected={200, 404}, idempotent=True
        )
        return None if reply.status == 404 else _object(reply.body)

    async def replace(self, partition: str, document: Document, *, etag: str) -> None:
        reply = await self._call(
            "PUT",
            f"/{_valid_id(document['id'])}",
            partition,
            body=document,
            headers={"If-Match": etag},
            expected={200, 404, 412},
        )
        if reply.status != 200:
            raise DocumentConflict

    async def find(self, partition: str, **equals: str) -> list[Document]:
        if not all(_FIELD.fullmatch(name) for name in equals):
            raise InputValidationError("Cosmos query field names are invalid")
        where = " AND ".join(f"c.{name} = @p{i}" for i, name in enumerate(equals))
        body = {
            "query": "SELECT * FROM c" + (f" WHERE {where}" if where else ""),
            "parameters": [{"name": f"@p{i}", "value": v} for i, v in enumerate(equals.values())],
        }
        found: list[Document] = []
        continuation: str | None = None
        for _ in range(_MAX_PAGES):
            headers = {
                "Content-Type": "application/query+json",
                "x-ms-documentdb-isquery": "True",
                "x-ms-max-item-count": str(_PAGE_SIZE),
            }
            if continuation:
                headers["x-ms-continuation"] = continuation
            reply = await self._call(
                "POST", "", partition, body=body, headers=headers, expected={200}, idempotent=True
            )
            documents = _object(reply.body).get("Documents")
            if not isinstance(documents, list):
                raise StorageError("Cosmos DB returned an unexpected response")
            found.extend(_object(item) for item in cast("list[object]", documents))
            continuation = reply.headers.get("x-ms-continuation")
            if not continuation:
                return found
        raise StorageError("Cosmos DB query returned too many pages")

    async def _call(
        self,
        method: str,
        suffix: str,
        partition: str,
        *,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        expected: set[int] | None = None,
        idempotent: bool = False,
    ) -> _Reply:
        try:
            return await with_retries(
                lambda: asyncio.to_thread(
                    self._send,
                    method,
                    suffix,
                    partition,
                    body,
                    headers or {},
                    expected or set(),
                    idempotent,
                ),
                tokens=self._tokens,
                sleep=self._sleep,
                monotonic=self._monotonic,
            )
        except Transient as failure:
            raise StorageError(str(failure)) from None
        except (StorageError, DocumentConflict):
            raise
        except Exception as error:
            raise StorageError("Cosmos DB request failed") from error

    def _send(
        self,
        method: str,
        suffix: str,
        partition: str,
        body: Any,
        headers: Mapping[str, str],
        expected: set[int],
        idempotent: bool,
    ) -> _Reply:
        signature = quote(f"type=aad&ver=1.0&sig={self._tokens.token()}", safe="")
        request = urllib.request.Request(
            f"{self._endpoint}{self._documents}{suffix}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Authorization": signature,
                "x-ms-date": formatdate(usegmt=True),
                "x-ms-version": API_VERSION,
                "x-ms-documentdb-partitionkey": json.dumps([partition]),
                "Content-Type": "application/json",
                "Accept": "application/json",
                **headers,
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status, raw, reply_headers = (
                    response.status,
                    response.read(_MAX_RESPONSE_BYTES + 1),
                    response.headers,
                )
        except urllib.error.HTTPError as error:
            if error.code in expected:
                return _Reply(error.code, None, {})
            message = f"Cosmos DB returned HTTP {error.code}"
            raise (transient_for(error, message) or StorageError(message)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if idempotent:
                raise Transient("Cosmos DB request failed", kind="network") from error
            raise StorageError("Cosmos DB request failed") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise StorageError("Cosmos DB response is too large")
        if status not in expected:
            raise StorageError(f"Cosmos DB returned HTTP {status}")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            raise StorageError("Cosmos DB returned invalid JSON") from None
        return _Reply(status, parsed, {k.lower(): v for k, v in reply_headers.items()})


def _valid_id(document_id: str) -> str:
    if not _DOCUMENT_ID.fullmatch(document_id):
        raise InputValidationError("Cosmos document id must be 64 lowercase hex characters")
    return document_id


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StorageError("Cosmos DB returned an unexpected response")
    return value  # pyright: ignore[reportUnknownVariableType]
