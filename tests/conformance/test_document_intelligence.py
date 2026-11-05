"""Prove the Document Intelligence client is bounded, checks what it trusts, and leaks nothing."""

from __future__ import annotations

import base64
import email.message
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any

import pytest

from agent_harness.adapters.extraction.document_intelligence import (
    API_VERSION,
    TOKEN_SCOPE,
    DocumentIntelligenceExtractor,
    validate_endpoint,
)
from agent_harness.adapters.http_support import NoRedirect
from agent_harness.core.errors import ExtractionError, InputValidationError

ENDPOINT = "https://di-test.cognitiveservices.azure.com"
HOST = "di-test.cognitiveservices.azure.com"
OPERATION = (
    f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/"
    f"abc-123?api-version={API_VERSION}"
)
PDF = b"%PDF-1.7\nbody"
DOCX = b"PK\x03\x04rest-of-zip"
MARKDOWN = "# Title\n\nSome body text.\n"


class Credential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []

    def get_token(self, *scopes: str) -> Any:
        self.scopes.append(scopes)
        return type("Token", (), {"token": "fake-token-value", "expires_on": 4_102_444_800})()


class Reply:
    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        self.headers = message
        self._body = io.BytesIO(body)

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


Script = Callable[[urllib.request.Request], tuple[int, Mapping[str, str], bytes]]


class Opener:
    """Answer requests from a script, raising like urllib does for error statuses."""

    def __init__(self, script: Script) -> None:
        self.script = script
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> Reply:
        self.requests.append(request)
        status, headers, body = self.script(request)
        if status >= 400:
            message = email.message.Message()
            for key, value in headers.items():
                message[key] = value
            raise urllib.error.HTTPError(
                request.full_url, status, "error", message, io.BytesIO(body)
            )
        return Reply(status, headers, body)


def succeeded(content: Any = MARKDOWN, **changes: Any) -> dict[str, Any]:
    analysis = {"contentFormat": "markdown", "content": content, "pages": [{"pageNumber": 1}]}
    analysis.update(changes)
    return {"status": "succeeded", "analyzeResult": analysis}


def service(polls: list[dict[str, Any]], *, location: str = OPERATION, retry: str = "1") -> Script:
    """Accept the document, then answer each poll from ``polls`` in order."""

    remaining = list(polls)

    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            return 202, {"Operation-Location": location, "Retry-After": retry}, b""
        return 200, {"Retry-After": retry}, json.dumps(remaining.pop(0)).encode()

    return script


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.pauses: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


def build(
    script: Script,
    *,
    credential: Credential | None = None,
    clock: Clock | None = None,
    **kwargs: Any,
) -> tuple[DocumentIntelligenceExtractor, Opener, Clock, Credential]:
    opener = Opener(script)
    clock = clock or Clock()
    credential = credential or Credential()
    extractor = DocumentIntelligenceExtractor(
        endpoint=ENDPOINT,
        credential=credential,
        opener=opener,  # pyright: ignore[reportArgumentType]
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return extractor, opener, clock, credential


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "name"), [(PDF, "handbook.pdf"), (DOCX, "Policy.DOCX")])
async def test_a_document_is_sent_once_and_returned_as_markdown(content: bytes, name: str) -> None:
    extractor, opener, _, credential = build(service([succeeded(pages=[{}, {}])]))

    document = await extractor.extract(content=content, filename=name)

    assert (document.markdown, document.page_count) == (MARKDOWN, 2)
    post, get = opener.requests
    assert post.full_url == (
        f"{ENDPOINT}/documentintelligence/documentModels/prebuilt-layout:analyze"
        f"?_overload=analyzeDocument&api-version={API_VERSION}&outputContentFormat=markdown"
    )
    assert post.get_method() == "POST" and get.get_method() == "GET"
    assert get.full_url == OPERATION
    assert post.get_header("Authorization") == "Bearer fake-token-value"
    assert post.get_header("Content-type") == "application/json"
    assert isinstance(post.data, bytes)
    assert json.loads(post.data) == {"base64Source": base64.b64encode(content).decode()}
    assert credential.scopes == [(TOKEN_SCOPE,)]


@pytest.mark.asyncio
async def test_the_file_name_never_reaches_the_service() -> None:
    extractor, opener, _, _ = build(service([succeeded()]))

    await extractor.extract(content=PDF, filename="Confidential-Merger-Plan.pdf")

    sent = " ".join(
        [
            request.full_url + str(request.header_items()) + str(request.data)
            for request in opener.requests
        ]
    )
    assert "Merger" not in sent and "Confidential" not in sent


@pytest.mark.asyncio
async def test_polling_waits_as_the_service_asks_within_bounds() -> None:
    polls = [{"status": "notStarted"}, {"status": "running"}, succeeded()]
    extractor, opener, clock, credential = build(service(polls, retry="3"))

    await extractor.extract(content=PDF, filename="a.pdf")

    assert clock.pauses == [3.0, 3.0, 3.0]
    assert len(opener.requests) == 4
    assert len(credential.scopes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry", "expected"), [("0", 1.0), ("-5", 1.0), ("999", 10.0), ("abc", 1.0), ("2.5", 2.5)]
)
async def test_retry_after_is_clamped_and_garbage_is_ignored(retry: str, expected: float) -> None:
    extractor, _, clock, _ = build(service([succeeded()], retry=retry))

    await extractor.extract(content=PDF, filename="a.pdf")

    assert clock.pauses == [expected]


@pytest.mark.asyncio
async def test_waiting_forever_is_not_possible() -> None:
    running = [{"status": "running"}] * 100
    extractor, opener, clock, _ = build(service(running, retry="5"), max_wait_seconds=20.0)

    with pytest.raises(ExtractionError, match="timed out"):
        await extractor.extract(content=PDF, filename="a.pdf")

    assert clock.now <= 20.0 and len(opener.requests) <= 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": "InvalidContent", "message": "secret-document-text"}, "(InvalidContent)"),
        ({"code": "bad code with spaces", "message": "secret-document-text"}, None),
        ({"code": "x" * 65}, None),
        ({"message": "secret-document-text"}, None),
        ("secret-document-text", None),
    ],
)
async def test_a_failed_analysis_reports_only_a_safe_code(error: Any, expected: str | None) -> None:
    extractor, _, _, _ = build(service([{"status": "failed", "error": error}]))

    with pytest.raises(ExtractionError) as raised:
        await extractor.extract(content=PDF, filename="a.pdf")

    assert "secret-document-text" not in str(raised.value)
    assert (expected in str(raised.value)) if expected else ("(" not in str(raised.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 500])
async def test_http_errors_report_status_and_code_but_not_bodies(status: int) -> None:
    body = json.dumps({"error": {"code": "InvalidContent", "message": "secret-document-text"}})

    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        return status, {}, body.encode()

    extractor, opener, _, _ = build(script)

    with pytest.raises(ExtractionError) as raised:
        await extractor.extract(content=PDF, filename="a.pdf")

    assert f"HTTP {status}, InvalidContent" in str(raised.value)
    assert "secret-document-text" not in str(raised.value) and "fake-token" not in str(raised.value)
    assert len(opener.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_throttling_is_retried_a_bounded_number_of_times(status: int) -> None:
    calls = {"n": 0}
    inner = service([succeeded()])

    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            calls["n"] += 1
            if calls["n"] < 3:
                return status, {"Retry-After": "2"}, b""
        return inner(request)

    extractor, _, clock, _ = build(script)

    document = await extractor.extract(content=PDF, filename="a.pdf")

    assert document.page_count == 1 and calls["n"] == 3
    assert clock.pauses[:2] == [2.0, 2.0]


@pytest.mark.asyncio
async def test_persistent_throttling_gives_up_after_three_attempts() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        return 429, {}, b""

    extractor, opener, _, _ = build(script)

    with pytest.raises(ExtractionError, match="HTTP 429"):
        await extractor.extract(content=PDF, filename="a.pdf")

    assert len(opener.requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        f"https://evil.example/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}",
        f"http://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}",
        f"https://user@{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}",
        f"https://{HOST}:8443/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}",
        f"https://{HOST}.evil.example/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}",
        f"https://{HOST}/documentintelligence/documentModels/custom/analyzeResults/a?api-version={API_VERSION}",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a/b?api-version={API_VERSION}",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/../x?api-version={API_VERSION}",
        f"https://{HOST}/other/path?api-version={API_VERSION}",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version=2000-01-01",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}&x=1",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a?api-version={API_VERSION}#frag",
        f"https://{HOST}/documentintelligence/documentModels/prebuilt-layout/analyzeResults/a",
        "",
    ],
)
async def test_an_operation_address_that_could_redirect_the_token_is_refused(
    location: str,
) -> None:
    extractor, opener, _, _ = build(service([succeeded()], location=location))

    with pytest.raises(ExtractionError, match="unexpected operation"):
        await extractor.extract(content=PDF, filename="a.pdf")

    assert [request.get_method() for request in opener.requests] == ["POST"]


@pytest.mark.asyncio
async def test_the_operation_header_name_is_case_insensitive() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            return 202, {"OPERATION-LOCATION": OPERATION}, b""
        return 200, {}, json.dumps(succeeded()).encode()

    extractor, _, _, _ = build(script)

    assert (await extractor.extract(content=PDF, filename="a.pdf")).markdown == MARKDOWN


@pytest.mark.asyncio
async def test_an_unknown_status_is_rejected_not_polled_forever() -> None:
    extractor, opener, _, _ = build(service([{"status": "unknown"}, succeeded()]))

    with pytest.raises(ExtractionError, match="unknown status"):
        await extractor.extract(content=PDF, filename="a.pdf")

    assert len(opener.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "poll",
    [
        {"status": "succeeded"},
        {"status": "succeeded", "analyzeResult": "text"},
        succeeded(contentFormat="text"),
        succeeded(content=""),
        succeeded(content="   \n"),
        succeeded(content=7),
        succeeded(content=None),
        succeeded(content="x" * 5_000_001),
        succeeded(pages=[]),
        succeeded(pages="1"),
        succeeded(pages=None),
        {},
    ],
)
async def test_results_that_are_not_clean_markdown_are_rejected(poll: dict[str, Any]) -> None:
    extractor, _, _, _ = build(service([poll]))

    with pytest.raises(ExtractionError):
        await extractor.extract(content=PDF, filename="a.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b"not json", b"[]", b"7", b'"text"'])
async def test_a_poll_that_is_not_a_json_object_is_rejected(raw: bytes) -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            return 202, {"Operation-Location": OPERATION}, b""
        return 200, {}, raw

    extractor, _, _, _ = build(script)

    with pytest.raises(ExtractionError):
        await extractor.extract(content=PDF, filename="a.pdf")


@pytest.mark.asyncio
async def test_a_poll_with_an_unexpected_status_code_is_rejected() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            return 202, {"Operation-Location": OPERATION}, b""
        return 204, {}, b""

    extractor, _, _, _ = build(script)

    with pytest.raises(ExtractionError, match="unexpected reply"):
        await extractor.extract(content=PDF, filename="a.pdf")


@pytest.mark.asyncio
async def test_acceptance_must_be_a_202() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        return 200, {"Operation-Location": OPERATION}, b"{}"

    extractor, _, _, _ = build(script)

    with pytest.raises(ExtractionError, match="did not accept"):
        await extractor.extract(content=PDF, filename="a.pdf")


@pytest.mark.asyncio
async def test_an_oversized_response_is_refused() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        if request.get_method() == "POST":
            return 202, {"Operation-Location": OPERATION}, b""
        return 200, {}, b"x" * (20 * 1024 * 1024 + 2)

    extractor, _, _, _ = build(script)

    with pytest.raises(ExtractionError, match="too large"):
        await extractor.extract(content=PDF, filename="a.pdf")


@pytest.mark.asyncio
async def test_transport_failures_do_not_leak_details() -> None:
    def script(request: urllib.request.Request) -> tuple[int, Mapping[str, str], bytes]:
        raise TimeoutError("host secret-host.example")

    extractor, _, _, _ = build(script)

    with pytest.raises(ExtractionError) as raised:
        await extractor.extract(content=PDF, filename="a.pdf")

    assert "secret-host" not in str(raised.value)
    assert isinstance(raised.value.__cause__, TimeoutError)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "name", "message"),
    [
        (DOCX, "old.doc", "Legacy .doc"),
        (DOCX, "old.DOC", "Legacy .doc"),
        (PDF, "notes.txt", "Unsupported document type"),
        (PDF, "noextension", "Unsupported document type"),
        (PDF, "archive.pdf.exe", "Unsupported document type"),
        (b"", "empty.pdf", "empty"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "encrypted.docx", "legacy or encrypted"),
        (b"plain text", "fake.pdf", "does not match"),
        (PDF, "pdf-as.docx", "does not match"),
        (DOCX, "docx-as.pdf", "does not match"),
        (b" %PDF-1.7", "leading-space.pdf", "does not match"),
    ],
)
async def test_files_are_checked_locally_before_anything_is_sent(
    content: bytes, name: str, message: str
) -> None:
    extractor, opener, _, credential = build(service([succeeded()]))

    with pytest.raises(InputValidationError, match=message):
        await extractor.extract(content=content, filename=name)

    assert opener.requests == [] and credential.scopes == []


@pytest.mark.asyncio
async def test_the_size_limit_is_enforced_before_sending() -> None:
    extractor, opener, _, _ = build(service([succeeded()]), max_file_bytes=len(PDF))

    await extractor.extract(content=PDF, filename="a.pdf")
    with pytest.raises(InputValidationError, match="exceeds"):
        await extractor.extract(content=PDF + b"x", filename="a.pdf")

    assert len(opener.requests) == 2


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://di-test.cognitiveservices.azure.com",
        "https://di-test.cognitiveservices.azure.com/",
        "https://DI-TEST.cognitiveservices.azure.com:443",
        "https://di-test.cognitiveservices.azure.us",
    ],
)
def test_resource_endpoints_are_accepted_and_canonicalized(endpoint: str) -> None:
    assert validate_endpoint(endpoint).startswith("https://di-test.cognitiveservices.")
    assert validate_endpoint(endpoint).islower()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://di-test.cognitiveservices.azure.com",
        "https://eastus.api.cognitive.microsoft.com",
        "https://di-test.cognitiveservices.azure.com.evil.example",
        "https://evil.example/di-test.cognitiveservices.azure.com",
        "https://user@di-test.cognitiveservices.azure.com",
        "https://di-test.cognitiveservices.azure.com:8443",
        "https://di-test.cognitiveservices.azure.com/documentintelligence",
        "https://di-test.cognitiveservices.azure.com/?x=1",
        "https://.cognitiveservices.azure.com",
        "https://-bad.cognitiveservices.azure.com",
        "https://a.b.cognitiveservices.azure.com",
        "https://localhost",
        "",
    ],
)
def test_other_endpoints_are_rejected_before_any_token_is_requested(endpoint: str) -> None:
    credential = Credential()

    with pytest.raises(InputValidationError):
        DocumentIntelligenceExtractor(endpoint=endpoint, credential=credential)

    assert credential.scopes == []


@pytest.mark.parametrize(
    "changes",
    [
        {"max_file_bytes": 0},
        {"max_file_bytes": 101 * 1024 * 1024},
        {"request_timeout_seconds": 0.0},
        {"request_timeout_seconds": 121.0},
        {"max_wait_seconds": 0.0},
        {"max_wait_seconds": 901.0},
    ],
)
def test_limits_must_be_in_range(changes: dict[str, Any]) -> None:
    with pytest.raises(InputValidationError):
        DocumentIntelligenceExtractor(endpoint=ENDPOINT, credential=Credential(), **changes)


def test_the_default_opener_refuses_redirects() -> None:
    extractor = DocumentIntelligenceExtractor(endpoint=ENDPOINT, credential=Credential())

    opener = extractor._opener  # pyright: ignore[reportPrivateUsage]
    handlers: list[object] = vars(opener)["handlers"]

    assert any(isinstance(handler, NoRedirect) for handler in handlers)
