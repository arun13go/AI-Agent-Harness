"""Stand in for Cosmos DB: tenant partitions, ETags and one optional unique field."""

from __future__ import annotations

import asyncio
import copy

from agent_harness.adapters.storage.cosmos_stores import Document, DocumentConflict


class FakeDocumentBackend:
    """Yield between steps so concurrent callers interleave as they would over a network."""

    def __init__(self, unique_field: str | None = None) -> None:
        self._unique = unique_field
        self._documents: dict[tuple[str, str], Document] = {}
        self._etags = 0

    def _stamp(self, document: Document) -> Document:
        self._etags += 1
        return {**copy.deepcopy(document), "_etag": str(self._etags)}

    async def create(self, partition: str, document: Document) -> None:
        await asyncio.sleep(0)
        if (partition, document["id"]) in self._documents:
            raise DocumentConflict
        if self._unique and any(
            key[0] == partition and stored[self._unique] == document[self._unique]
            for key, stored in self._documents.items()
        ):
            raise DocumentConflict
        self._documents[(partition, document["id"])] = self._stamp(document)

    async def read(self, partition: str, document_id: str) -> Document | None:
        await asyncio.sleep(0)
        found = self._documents.get((partition, document_id))
        return None if found is None else copy.deepcopy(found)

    async def replace(self, partition: str, document: Document, *, etag: str) -> None:
        await asyncio.sleep(0)
        found = self._documents.get((partition, document["id"]))
        if found is None or found["_etag"] != etag:
            raise DocumentConflict
        self._documents[(partition, document["id"])] = self._stamp(document)

    async def find(self, partition: str, **equals: str) -> list[Document]:
        await asyncio.sleep(0)
        return [
            copy.deepcopy(stored)
            for key, stored in self._documents.items()
            if key[0] == partition and all(stored.get(f) == v for f, v in equals.items())
        ]
