"""Synchronize a set of evidence chunks into one corpus of an Azure AI Search index.

A sync makes the index hold exactly the chunks it is given for one tenant and corpus: new and
changed chunks are written, chunks that no longer exist are removed, and every current chunk is
rewritten so its freshness window restarts. Chunk identifiers are deterministic and include the
source version, so an edited file replaces its own chunks and an untouched file changes nothing
except freshness.

The sync is conservative. It refuses to touch another corpus's chunks, writes before it deletes so
a failure leaves extra evidence rather than missing evidence, supports a dry run, and refuses a
large deletion unless asked. Reports carry counts only, never source names or content.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent_harness.adapters.retrieval.azure_search import (
    CORPUS_FIELD,
    build_corpus_filter,
    build_id_filter,
    evidence_to_document,
    validate_corpus_id,
)
from agent_harness.core.errors import InputValidationError
from agent_harness.core.governance import validate_tenant_scope
from agent_harness.core.types import RetrievedItem

# Chunk identifiers are about 36 characters; this keeps one collision query well under URL and
# filter limits.
_COLLISION_BATCH = 100
_LARGE_DELETE_FLOOR = 10


class IngestBackend(Protocol):
    """The index operations a sync needs."""

    async def create_index(self) -> None: ...

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int: ...

    async def delete_documents(self, document_ids: Sequence[str]) -> int: ...

    async def list_documents(
        self, *, odata_filter: str, select: Sequence[str]
    ) -> list[dict[str, Any]]: ...


async def sync_corpus(
    backend: IngestBackend,
    *,
    items: Sequence[RetrievedItem],
    tenant_scope: str,
    corpus_id: str,
    dry_run: bool = False,
    allow_large_delete: bool = False,
    allow_empty: bool = False,
) -> dict[str, object]:
    """Make the corpus hold exactly ``items``, and report what changed as counts."""

    validate_tenant_scope(tenant_scope)
    validate_corpus_id(corpus_id)
    desired = _index_by_id(items, tenant_scope)
    if not dry_run:
        await backend.create_index()
    owned = await backend.list_documents(
        odata_filter=build_corpus_filter(tenant_scope, corpus_id), select=["id"]
    )
    existing = {str(document["id"]) for document in owned}
    await _refuse_foreign_collisions(backend, set(desired), corpus_id)

    added = sorted(set(desired) - existing)
    removed = sorted(existing - set(desired))
    _refuse_unsafe_deletion(
        existing=len(existing),
        removed=len(removed),
        desired=len(desired),
        allow_large_delete=allow_large_delete,
        allow_empty=allow_empty,
    )
    if not dry_run:
        # Write first: if the delete fails, the index keeps extra evidence, never less.
        await backend.upload(
            [evidence_to_document(item, corpus_id=corpus_id) for item in desired.values()]
        )
        if removed:
            await backend.delete_documents(removed)
    return {
        "action": "sync",
        "dryRun": dry_run,
        "desired": len(desired),
        "added": len(added),
        "refreshed": len(desired) - len(added),
        "removed": len(removed),
    }


def _index_by_id(items: Sequence[RetrievedItem], tenant_scope: str) -> dict[str, RetrievedItem]:
    desired: dict[str, RetrievedItem] = {}
    for item in items:
        if item.tenant_scope != tenant_scope:
            raise InputValidationError("Every chunk must belong to the tenant being synchronized")
        if item.item_id in desired:
            raise InputValidationError("Chunk identifiers must be unique within a sync")
        desired[item.item_id] = item
    return desired


async def _refuse_foreign_collisions(
    backend: IngestBackend, desired_ids: set[str], corpus_id: str
) -> None:
    """Refuse to overwrite a chunk that another corpus (or no corpus) already owns."""

    ordered = sorted(desired_ids)
    for start in range(0, len(ordered), _COLLISION_BATCH):
        batch = ordered[start : start + _COLLISION_BATCH]
        present = await backend.list_documents(
            odata_filter=build_id_filter(batch), select=["id", CORPUS_FIELD]
        )
        if any(document.get(CORPUS_FIELD) != corpus_id for document in present):
            raise InputValidationError(
                "A chunk already exists in another corpus or was added without a corpus; "
                "use a different tenant or distinct source paths for each corpus"
            )


def _refuse_unsafe_deletion(
    *,
    existing: int,
    removed: int,
    desired: int,
    allow_large_delete: bool,
    allow_empty: bool,
) -> None:
    if desired == 0 and existing > 0 and not allow_empty:
        raise InputValidationError(
            "This sync would delete every chunk in the corpus; pass allow_empty to confirm"
        )
    if (
        existing >= _LARGE_DELETE_FLOOR
        and removed * 2 > existing
        and not allow_large_delete
        and desired > 0
    ):
        raise InputValidationError(
            f"This sync would delete {removed} of {existing} chunks; "
            "pass allow_large_delete to confirm"
        )
