"""Call the Azure AI Search REST API with an Entra token, without a service SDK.

The endpoint is validated before any token is requested, redirects are refused, and responses are
size-bounded, so a mistaken or hostile endpoint cannot receive a credential or exhaust memory.
Errors carry the HTTP status only, never the response body, which can echo query or document text.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
from agent_harness.adapters.retrieval.azure_search import INDEX_FIELDS, SCORE_KEY
from agent_harness.core.errors import InputValidationError, RetrievalError

API_VERSION = "2024-07-01"
SEMANTIC_CONFIGURATION = "default"
RERANKER_KEY = "@search.rerankerScore"
TOKEN_SCOPE = "https://search.azure.com/.default"
_ALLOWED_HOST_SUFFIXES = (".search.windows.net", ".search.azure.us", ".search.azure.cn")
_SERVICE_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$")
_INDEX_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_UPLOAD_BATCH = 500
_LIST_PAGE_SIZE = 1000
_MAX_LISTED_DOCUMENTS = 100_000
_FIELD_NAMES = frozenset(field["name"] for field in INDEX_FIELDS)
_SELECT = ",".join(field["name"] for field in INDEX_FIELDS)


def validate_endpoint(endpoint: str) -> str:
    """Return the canonical service URL, or reject anything that is not a Search service host."""

    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    suffix = next((item for item in _ALLOWED_HOST_SUFFIXES if host.endswith(item)), None)
    label = host[: -len(suffix)] if suffix else ""
    if (
        parts.scheme != "https"
        or suffix is None
        or not _SERVICE_HOST.fullmatch(label)
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise InputValidationError("Search endpoint must be an https Azure AI Search service URL")
    return f"https://{host}"


def index_definition(index_name: str) -> dict[str, Any]:
    """Build the index definition from ``INDEX_FIELDS`` with every attribute stated explicitly."""

    fields: list[dict[str, Any]] = []
    for spec in INDEX_FIELDS:
        fields.append(
            {
                "name": spec["name"],
                "type": spec["type"],
                "key": bool(spec.get("key", False)),
                "searchable": bool(spec.get("searchable", False)),
                "filterable": bool(spec.get("filterable", False)),
                "sortable": bool(spec.get("sortable", False)),
                "facetable": False,
                "retrievable": True,
            }
        )
    return {
        "name": _index_name(index_name),
        "fields": fields,
        "semantic": {
            "configurations": [
                {
                    "name": SEMANTIC_CONFIGURATION,
                    "prioritizedFields": {"prioritizedContentFields": [{"fieldName": "content"}]},
                }
            ]
        },
    }


class AzureSearchRestBackend:
    """Query one index through the REST API; implements the provider's ``SearchBackend`` port.

    With ``semantic=True`` the service re-ranks the top results and the backend reports the
    reranker score (0 to 4) as the result score. Results the service did not re-rank have no such
    score and are reported as 0, so the provider drops them.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        index_name: str,
        credential: TokenCredential,
        timeout_seconds: float = 15.0,
        opener: urllib.request.OpenerDirector | None = None,
        clock: Callable[[], float] = time.time,
        semantic: bool = False,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0.0 < timeout_seconds <= 120.0:
            raise InputValidationError("Search timeout must be between 0 and 120 seconds")
        self._endpoint = validate_endpoint(endpoint)
        self._index = _index_name(index_name)
        self._timeout = timeout_seconds
        self._opener = opener or urllib.request.build_opener(NoRedirect)
        self._tokens = BearerTokenCache(credential, TOKEN_SCOPE, clock=clock)
        self._semantic = semantic
        self._sleep = sleep
        self._monotonic = monotonic

    def _bearer_token(self) -> str:
        return self._tokens.token()

    async def search(
        self, *, text: str, odata_filter: str, top: int
    ) -> Sequence[Mapping[str, Any]]:
        """Run a keyword search, or a semantic one, with the scope filter applied by the service."""

        body: dict[str, Any] = {
            "search": text,
            "queryType": "semantic" if self._semantic else "simple",
            "searchMode": "any",
            "filter": odata_filter,
            "top": top,
            "select": _SELECT,
        }
        if self._semantic:
            body["semanticConfiguration"] = SEMANTIC_CONFIGURATION
        payload = await self._call("POST", f"/indexes/{self._index}/docs/search", body)
        hits = _objects(payload.get("value"), "response")
        if not self._semantic:
            return hits
        return [{**hit, SCORE_KEY: _reranker_score(hit)} for hit in hits]

    async def create_index(self) -> None:
        """Create or update the index definition (idempotent)."""

        await self._call("PUT", f"/indexes/{self._index}", index_definition(self._index))

    async def delete_index(self) -> None:
        """Delete the index; used to clean up experiments."""

        await self._call("DELETE", f"/indexes/{self._index}", None)

    async def merge_documents(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """Update only the named fields of existing documents, in bounded batches.

        Every document must exist: a merge into a missing key is reported as a failure, never
        created, so a refresh can not resurrect evidence that was removed in the meantime.
        """

        merged = 0
        for start in range(0, len(documents), _MAX_UPLOAD_BATCH):
            batch = [
                {"@search.action": "merge", **document}
                for document in documents[start : start + _MAX_UPLOAD_BATCH]
            ]
            payload = await self._call(
                "POST", f"/indexes/{self._index}/docs/index", {"value": batch}
            )
            results = _objects(payload.get("value"), "merge result")
            if len(results) != len(batch) or not all(r.get("status") is True for r in results):
                raise RetrievalError("Search backend rejected one or more updates")
            merged += len(batch)
        return merged

    async def delete_documents(self, document_ids: Sequence[str]) -> int:
        """Delete documents by key in bounded batches and require every request to succeed."""

        deleted = 0
        for start in range(0, len(document_ids), _MAX_UPLOAD_BATCH):
            batch = [
                {"@search.action": "delete", "id": document_id}
                for document_id in document_ids[start : start + _MAX_UPLOAD_BATCH]
            ]
            payload = await self._call(
                "POST", f"/indexes/{self._index}/docs/index", {"value": batch}
            )
            results = _objects(payload.get("value"), "delete result")
            if len(results) != len(batch) or not all(r.get("status") is True for r in results):
                raise RetrievalError("Search backend rejected one or more deletions")
            deleted += len(batch)
        return deleted

    async def list_documents(
        self, *, odata_filter: str, select: Sequence[str]
    ) -> list[dict[str, Any]]:
        """Return every document matching the filter, in key order, with the selected fields.

        Pages by key rather than by offset so the walk has no service paging ceiling and stays
        stable while documents are written. The result is bounded.
        """

        fields = [name for name in select if name in _FIELD_NAMES]
        if not fields or len(fields) != len(select) or "id" not in fields:
            raise InputValidationError("Selected fields must be index fields and include 'id'")
        found: list[dict[str, Any]] = []
        last_key: str | None = None
        while True:
            page_filter = odata_filter
            if last_key is not None:
                page_filter = f"({odata_filter}) and id gt {_odata_string(last_key)}"
            payload = await self._call(
                "POST",
                f"/indexes/{self._index}/docs/search",
                {
                    "search": "*",
                    "queryType": "simple",
                    "filter": page_filter,
                    "orderby": "id asc",
                    "top": _LIST_PAGE_SIZE,
                    "select": ",".join(fields),
                },
            )
            page = _objects(payload.get("value"), "response")
            found.extend(page)
            if len(found) > _MAX_LISTED_DOCUMENTS:
                raise RetrievalError("Search backend returned too many documents to list")
            if len(page) < _LIST_PAGE_SIZE:
                return found
            key = page[-1].get("id")
            if not isinstance(key, str) or (last_key is not None and key <= last_key):
                raise RetrievalError("Search backend returned an unexpected page order")
            last_key = key

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """Upload documents in bounded batches and require every one to succeed."""

        uploaded = 0
        for start in range(0, len(documents), _MAX_UPLOAD_BATCH):
            batch = [
                {"@search.action": "mergeOrUpload", **document}
                for document in documents[start : start + _MAX_UPLOAD_BATCH]
            ]
            payload = await self._call(
                "POST", f"/indexes/{self._index}/docs/index", {"value": batch}
            )
            results = _objects(payload.get("value"), "upload result")
            if len(results) != len(batch):
                raise RetrievalError("Search backend returned an unexpected upload result")
            if not all(result.get("status") is True for result in results):
                raise RetrievalError("Search backend rejected one or more documents")
            uploaded += len(batch)
        return uploaded

    async def _call(self, method: str, path: str, body: Any) -> dict[str, Any]:
        """Send one request; every operation here is idempotent, so transient failures retry."""

        try:
            return await with_retries(
                lambda: asyncio.to_thread(self._request, method, path, body),
                tokens=self._tokens,
                sleep=self._sleep,
                monotonic=self._monotonic,
            )
        except Transient as failure:
            raise RetrievalError(str(failure)) from None
        except RetrievalError:
            raise
        except Exception as error:
            raise RetrievalError("Search backend request failed") from error

    def _request(self, method: str, path: str, body: Any) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self._endpoint}{path}?api-version={quote(API_VERSION)}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._bearer_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            message = f"Search backend returned HTTP {error.code}"
            raise (transient_for(error, message) or RetrievalError(message)) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise Transient("Search backend request failed", kind="network") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise RetrievalError("Search backend response is too large")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise RetrievalError("Search backend returned invalid JSON") from None
        if not isinstance(parsed, dict):
            raise RetrievalError("Search backend returned an unexpected response")
        return parsed  # pyright: ignore[reportUnknownVariableType]


def _odata_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _reranker_score(hit: Mapping[str, Any]) -> float:
    score = hit.get(RERANKER_KEY)
    if score is None:
        return 0.0
    if isinstance(score, bool) or not isinstance(score, int | float) or not 0.0 <= score <= 4.0:
        raise RetrievalError("Search backend returned an invalid reranker score")
    return float(score)


def _objects(value: object, what: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RetrievalError(f"Search backend returned an unexpected {what}")
    entries = cast("list[object]", value)
    objects = [cast("dict[str, Any]", entry) for entry in entries if isinstance(entry, dict)]
    if len(objects) != len(entries):
        raise RetrievalError(f"Search backend returned an unexpected {what}")
    return objects


def _index_name(name: str) -> str:
    if not _INDEX_NAME.fullmatch(name):
        raise InputValidationError("Search index name must be lowercase letters, digits or dashes")
    return name
