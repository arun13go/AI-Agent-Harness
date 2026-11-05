"""Prove the REST backend lists and deletes documents in bounded, ordered, validated batches."""

from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from agent_harness.adapters.retrieval.azure_search_rest import AzureSearchRestBackend
from agent_harness.core.errors import InputValidationError, RetrievalError
from tests.conformance.test_azure_search_rest import ENDPOINT, Credential, FakeOpener, body_of


def backend(
    handler: Callable[[urllib.request.Request], bytes],
) -> tuple[AzureSearchRestBackend, FakeOpener]:
    opener = FakeOpener(handler)
    return (
        AzureSearchRestBackend(
            endpoint=ENDPOINT,
            index_name="harness-chunks",
            credential=Credential(),
            opener=opener,  # pyright: ignore[reportArgumentType]
        ),
        opener,
    )


def paged(keys: list[str]) -> Callable[[urllib.request.Request], bytes]:
    """Serve the sorted keys in pages, honoring the ``id gt`` cursor like the service."""

    ordered = sorted(keys)

    def handler(request: urllib.request.Request) -> bytes:
        body = body_of(request)
        assert body["orderby"] == "id asc"
        after = re.search(r"id gt '((?:[^']|'')*)'", body["filter"])
        cursor = after.group(1).replace("''", "'") if after else None
        remaining = [key for key in ordered if cursor is None or key > cursor]
        return json.dumps({"value": [{"id": key} for key in remaining[: body["top"]]]}).encode()

    return handler


@pytest.mark.asyncio
async def test_listing_walks_every_page_in_key_order_with_a_cursor() -> None:
    keys = [f"k{n:05d}" for n in range(2500)]
    service, opener = backend(paged(keys))

    found = await service.list_documents(odata_filter="tenantScope eq 'x'", select=["id"])

    assert [document["id"] for document in found] == sorted(keys)
    filters = [body_of(request)["filter"] for request in opener.requests]
    assert len(filters) == 3
    assert filters[0] == "tenantScope eq 'x'"
    assert filters[1] == "(tenantScope eq 'x') and id gt 'k00999'"
    assert filters[2] == "(tenantScope eq 'x') and id gt 'k01999'"
    sent = body_of(opener.requests[0])
    assert (sent["search"], sent["queryType"], sent["top"], sent["select"]) == (
        "*",
        "simple",
        1000,
        "id",
    )


@pytest.mark.asyncio
async def test_listing_a_short_result_takes_one_request() -> None:
    service, opener = backend(paged(["a", "b"]))

    assert [d["id"] for d in await service.list_documents(odata_filter="f", select=["id"])] == [
        "a",
        "b",
    ]
    assert len(opener.requests) == 1


@pytest.mark.asyncio
async def test_a_cursor_quote_is_escaped() -> None:
    keys = [f"k{n:05d}" for n in range(999)] + ["z'q"]
    service, opener = backend(paged(keys))

    await service.list_documents(odata_filter="f", select=["id"])

    assert [body_of(request)["filter"] for request in opener.requests] == [
        "f",
        "(f) and id gt 'z''q'",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "select", [[], ["content"], ["id", "unknown"], ["sourceId"], ["id", "id", "nope"]]
)
async def test_listing_rejects_selections_that_are_not_index_fields_with_the_key(
    select: list[str],
) -> None:
    service, opener = backend(paged([]))

    with pytest.raises(InputValidationError):
        await service.list_documents(odata_filter="f", select=select)
    assert opener.requests == []


@pytest.mark.asyncio
async def test_listing_refuses_a_backend_that_does_not_advance() -> None:
    stuck = json.dumps({"value": [{"id": "same"}] * 1000}).encode()
    service, _ = backend(lambda _: stuck)

    with pytest.raises(RetrievalError, match="page order"):
        await service.list_documents(odata_filter="f", select=["id"])


@pytest.mark.asyncio
async def test_listing_is_bounded() -> None:
    counter = {"n": 0}

    def endless(_: urllib.request.Request) -> bytes:
        start = counter["n"]
        counter["n"] += 1000
        return json.dumps({"value": [{"id": f"k{start + n:08d}"} for n in range(1000)]}).encode()

    service, opener = backend(endless)

    with pytest.raises(RetrievalError, match="too many documents"):
        await service.list_documents(odata_filter="f", select=["id"])
    assert len(opener.requests) == 101


@pytest.mark.asyncio
async def test_deletion_batches_and_sends_delete_actions() -> None:
    calls: list[list[dict[str, Any]]] = []

    def handler(request: urllib.request.Request) -> bytes:
        batch = body_of(request)["value"]
        calls.append(batch)
        return json.dumps(
            {"value": [{"key": entry["id"], "status": True} for entry in batch]}
        ).encode()

    service, _ = backend(handler)

    assert await service.delete_documents([f"k{n}" for n in range(1001)]) == 1001
    assert [len(batch) for batch in calls] == [500, 500, 1]
    assert all(set(entry) == {"@search.action", "id"} for batch in calls for entry in batch)
    assert all(entry["@search.action"] == "delete" for batch in calls for entry in batch)


@pytest.mark.asyncio
async def test_deleting_nothing_makes_no_request() -> None:
    service, opener = backend(paged([]))

    assert await service.delete_documents([]) == 0
    assert opener.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [{"value": [{"key": "a", "status": False}]}, {"value": []}, {"value": [{"key": "a"}]}, {}],
)
async def test_failed_or_missing_deletion_results_are_rejected(reply: dict[str, Any]) -> None:
    service, _ = backend(lambda _: json.dumps(reply).encode())

    with pytest.raises(RetrievalError):
        await service.delete_documents(["a"])


@pytest.mark.asyncio
async def test_merging_batches_and_sends_merge_actions_with_only_the_named_fields() -> None:
    calls: list[list[dict[str, Any]]] = []

    def handler(request: urllib.request.Request) -> bytes:
        batch = body_of(request)["value"]
        calls.append(batch)
        return json.dumps(
            {"value": [{"key": entry["id"], "status": True} for entry in batch]}
        ).encode()

    service, _ = backend(handler)
    documents = [{"id": f"k{n}", "freshUntil": "2026-12-01T00:00:00+00:00"} for n in range(501)]

    assert await service.merge_documents(documents) == 501
    assert [len(batch) for batch in calls] == [500, 1]
    assert all(entry["@search.action"] == "merge" for batch in calls for entry in batch)
    assert all(set(entry) == {"@search.action", "id", "freshUntil"} for b in calls for entry in b)


@pytest.mark.asyncio
async def test_merging_nothing_makes_no_request() -> None:
    service, opener = backend(paged([]))

    assert await service.merge_documents([]) == 0
    assert opener.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [{"value": [{"key": "a", "status": False}]}, {"value": []}, {"value": [{"key": "a"}]}, {}],
)
async def test_a_merge_into_a_missing_document_or_a_bad_reply_is_rejected(
    reply: dict[str, Any],
) -> None:
    service, _ = backend(lambda _: json.dumps(reply).encode())

    with pytest.raises(RetrievalError, match=r"rejected|unexpected"):
        await service.merge_documents([{"id": "a", "freshUntil": "x"}])
