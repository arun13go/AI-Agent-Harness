"""Extract Markdown from PDF and Word files with Azure AI Document Intelligence.

The documents are attacker-controlled, so parsing happens in the service and never in this
process. This client's job is to send bytes, wait a bounded time, and treat the answer as
untrusted text. It validates the endpoint and the operation address before any token is sent,
refuses redirects, bounds sizes and waiting, never sends the file name, and reports errors with a
status and a service error code only, never a response body.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from agent_harness.adapters.http_support import BearerTokenCache, NoRedirect, TokenCredential
from agent_harness.core.errors import ExtractionError, InputValidationError
from agent_harness.core.types import ExtractedDocument

API_VERSION = "2024-11-30"
MODEL_ID = "prebuilt-layout"
TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"
SUPPORTED_EXTENSIONS = (".pdf", ".docx")

# Entra authentication needs the resource's own subdomain; regional endpoints would not accept it.
_HOST_SUFFIXES = (
    ".cognitiveservices.azure.com",
    ".cognitiveservices.azure.us",
    ".cognitiveservices.azure.cn",
)
_SERVICE_HOST = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_OPERATION_PATH = re.compile(
    rf"^/documentintelligence/documentModels/{MODEL_ID}/analyzeResults/[A-Za-z0-9-]{{1,64}}$"
)
_ERROR_CODE = re.compile(r"^[A-Za-z0-9]{1,64}$")
_MAGIC = {".pdf": b"%PDF-", ".docx": b"PK\x03\x04"}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAX_RESULT_BYTES = 20 * 1024 * 1024
_MAX_MARKDOWN_CHARACTERS = 5_000_000
_MAX_ATTEMPTS = 3
_MAX_PAUSE_SECONDS = 10.0
_RETRYABLE = frozenset({429, 503})


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    headers: Mapping[str, str]
    body: bytes


def validate_endpoint(endpoint: str) -> str:
    """Return the canonical service URL, or reject anything that is not a resource subdomain."""

    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    suffix = next((item for item in _HOST_SUFFIXES if host.endswith(item)), None)
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
        raise InputValidationError(
            "Document Intelligence endpoint must be the https URL of a resource with a custom "
            "subdomain, ending in cognitiveservices.azure.com"
        )
    return f"https://{host}"


class DocumentIntelligenceExtractor:
    """Implements ``DocumentExtractor`` over the Document Intelligence layout model."""

    def __init__(
        self,
        *,
        endpoint: str,
        credential: TokenCredential,
        max_file_bytes: int = 4 * 1024 * 1024,
        request_timeout_seconds: float = 30.0,
        max_wait_seconds: float = 120.0,
        opener: urllib.request.OpenerDirector | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        token_clock: Callable[[], float] = time.time,
    ) -> None:
        if not 1 <= max_file_bytes <= 100 * 1024 * 1024:
            raise InputValidationError("Maximum file size must be between 1 byte and 100 MiB")
        if not 0.0 < request_timeout_seconds <= 120.0 or not 0.0 < max_wait_seconds <= 900.0:
            raise InputValidationError("Extraction timeouts are out of range")
        self._endpoint = validate_endpoint(endpoint)
        self._host = urlsplit(self._endpoint).hostname or ""
        self._tokens = BearerTokenCache(credential, TOKEN_SCOPE, clock=token_clock)
        self._max_file_bytes = max_file_bytes
        self._timeout = request_timeout_seconds
        self._max_wait = max_wait_seconds
        self._opener = opener or urllib.request.build_opener(NoRedirect)
        self._clock = clock
        self._sleep = sleep

    async def extract(self, *, content: bytes, filename: str) -> ExtractedDocument:
        """Return the document as Markdown, or fail closed."""

        self._check_file(content, filename)
        analyze_url = (
            f"{self._endpoint}/documentintelligence/documentModels/{MODEL_ID}:analyze"
            f"?_overload=analyzeDocument&api-version={API_VERSION}&outputContentFormat=markdown"
        )
        body = json.dumps({"base64Source": base64.b64encode(content).decode("ascii")}).encode()
        reply = await self._call("POST", analyze_url, body)
        if reply.status != 202:
            raise ExtractionError("Document extraction service did not accept the document")
        operation_url = self._operation_url(reply.headers.get("operation-location", ""))
        return await self._await_result(operation_url, reply)

    def _check_file(self, content: bytes, filename: str) -> None:
        extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension == ".doc":
            raise InputValidationError(
                "Legacy .doc files are not supported; save the document as .docx"
            )
        if extension not in SUPPORTED_EXTENSIONS:
            raise InputValidationError(
                f"Unsupported document type '{extension}'; supported: "
                + ", ".join(SUPPORTED_EXTENSIONS)
            )
        if not content:
            raise InputValidationError("Document is empty")
        if len(content) > self._max_file_bytes:
            raise InputValidationError(f"Document exceeds {self._max_file_bytes} bytes")
        if content.startswith(_OLE_MAGIC):
            raise InputValidationError(
                "Document is a legacy or encrypted Office file; save it as an unencrypted .docx"
            )
        if not content.startswith(_MAGIC[extension]):
            raise InputValidationError("Document content does not match its file extension")

    def _operation_url(self, location: str) -> str:
        parts = urlsplit(location)
        if (
            parts.scheme != "https"
            or (parts.hostname or "").lower() != self._host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or not _OPERATION_PATH.fullmatch(parts.path)
            or parts.query != f"api-version={API_VERSION}"
            or parts.fragment
        ):
            raise ExtractionError("Document extraction service returned an unexpected operation")
        return f"https://{self._host}{parts.path}?{parts.query}"

    async def _await_result(self, operation_url: str, first: _Reply) -> ExtractedDocument:
        deadline = self._clock() + self._max_wait
        pause = _pause(first.headers.get("retry-after"), 1.0)
        while True:
            if self._clock() + pause > deadline:
                raise ExtractionError("Document extraction timed out")
            await self._sleep(pause)
            reply = await self._call("GET", operation_url, None)
            if reply.status != 200:
                raise ExtractionError("Document extraction service returned an unexpected reply")
            result = _json_object(reply.body)
            status = result.get("status")
            if status == "succeeded":
                return _document(result)
            if status == "failed":
                raise ExtractionError(_failure(result))
            if status not in ("notStarted", "running"):
                raise ExtractionError("Document extraction service returned an unknown status")
            pause = _pause(reply.headers.get("retry-after"), min(pause * 1.5, _MAX_PAUSE_SECONDS))

    async def _call(self, method: str, url: str, body: bytes | None) -> _Reply:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                reply = await asyncio.to_thread(self._send, method, url, body)
            except ExtractionError:
                raise
            except Exception as error:
                raise ExtractionError("Document extraction request failed") from error
            if reply.status in _RETRYABLE and attempt < _MAX_ATTEMPTS:
                await self._sleep(_pause(reply.headers.get("retry-after"), float(attempt)))
                continue
            if reply.status >= 400:
                raise ExtractionError(_http_failure(reply))
            return reply
        raise ExtractionError("Document extraction request failed")  # pragma: no cover

    def _send(self, method: str, url: str, body: bytes | None) -> _Reply:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self._tokens.token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESULT_BYTES + 1)
                status = int(response.status)
                headers = {key.lower(): str(value) for key, value in response.headers.items()}
        except urllib.error.HTTPError as error:
            return _Reply(
                error.code,
                {key.lower(): str(value) for key, value in error.headers.items()},
                error.read(65536),
            )
        if len(raw) > _MAX_RESULT_BYTES:
            raise ExtractionError("Document extraction response is too large")
        return _Reply(status, headers, raw)


def _pause(retry_after: str | None, fallback: float) -> float:
    """Wait as the service asks, within sane bounds."""

    try:
        requested = float(retry_after) if retry_after is not None else fallback
    except ValueError:
        requested = fallback
    return min(max(requested, 1.0), _MAX_PAUSE_SECONDS)


def _object(value: object) -> dict[str, Any] | None:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        raise ExtractionError("Document extraction service returned invalid JSON") from None
    result = _object(parsed)
    if result is None:
        raise ExtractionError("Document extraction service returned an unexpected reply")
    return result


def _error_code(container: Mapping[str, Any] | None) -> str | None:
    error = _object(container.get("error")) if container is not None else None
    code = error.get("code") if error is not None else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else None


def _failure(result: Mapping[str, Any]) -> str:
    code = _error_code(result)
    return f"Document extraction failed ({code})" if code else "Document extraction failed"


def _http_failure(reply: _Reply) -> str:
    try:
        parsed: object = json.loads(reply.body)
    except ValueError:
        parsed = None
    code = _error_code(_object(parsed))
    detail = f", {code}" if code else ""
    return f"Document extraction service returned HTTP {reply.status}{detail}"


def _document(result: Mapping[str, Any]) -> ExtractedDocument:
    analysis = _object(result.get("analyzeResult"))
    if analysis is None:
        raise ExtractionError("Document extraction service returned no result")
    content = analysis.get("content")
    pages = analysis.get("pages")
    if analysis.get("contentFormat") != "markdown":
        raise ExtractionError("Document extraction service did not return Markdown")
    if not isinstance(content, str) or not content.strip():
        raise ExtractionError("Document contains no extractable text")
    if len(content) > _MAX_MARKDOWN_CHARACTERS:
        raise ExtractionError("Extracted text is too large")
    if not isinstance(pages, list) or not pages:
        raise ExtractionError("Document extraction service returned no pages")
    return ExtractedDocument(markdown=content, page_count=len(cast("list[object]", pages)))
