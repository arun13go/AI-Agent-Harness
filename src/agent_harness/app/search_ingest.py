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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from agent_harness.adapters.retrieval.azure_search import (
    CORPUS_FIELD,
    LABELS_FIELD,
    build_corpus_filter,
    build_id_filter,
    evidence_to_document,
    validate_corpus_id,
)
from agent_harness.core.errors import InputValidationError
from agent_harness.core.governance import validate_authorization_labels, validate_tenant_scope
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

    async def merge_documents(self, documents: Sequence[Mapping[str, Any]]) -> int: ...

    async def list_documents(
        self, *, odata_filter: str, select: Sequence[str]
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class Refresh:
    """The freshness window and labels to apply to chunks kept without re-extraction."""

    ingested_at: datetime
    fresh_until: datetime
    authorization_labels: tuple[str, ...]


async def known_source_versions(
    backend: IngestBackend, *, tenant_scope: str, corpus_id: str
) -> dict[str, str]:
    """Return the version the index holds for each source of a corpus.

    A source whose chunks disagree on version is left out, so it is simply extracted again.
    """

    owned = await backend.list_documents(
        odata_filter=build_corpus_filter(tenant_scope, corpus_id),
        select=["id", "sourceId", "sourceVersion"],
    )
    versions: dict[str, set[str]] = {}
    for document in owned:
        source, version = document.get("sourceId"), document.get("sourceVersion")
        if isinstance(source, str) and isinstance(version, str):
            versions.setdefault(source, set()).add(version)
    return {source: next(iter(found)) for source, found in versions.items() if len(found) == 1}


async def sync_corpus(
    backend: IngestBackend,
    *,
    items: Sequence[RetrievedItem],
    tenant_scope: str,
    corpus_id: str,
    dry_run: bool = False,
    allow_large_delete: bool = False,
    allow_empty: bool = False,
    unchanged_sources: Mapping[str, str] | None = None,
    refresh: Refresh | None = None,
) -> dict[str, object]:
    """Make the corpus hold exactly ``items`` plus the unchanged sources, and report counts.

    ``unchanged_sources`` names sources (with their versions) that were deliberately not
    re-extracted. Their chunks stay, with freshness and labels refreshed in place. If the index no
    longer holds a named source at that version, the sync stops instead of deleting its evidence.
    """

    validate_tenant_scope(tenant_scope)
    validate_corpus_id(corpus_id)
    unchanged = dict(unchanged_sources or {})
    if unchanged and refresh is None:
        raise InputValidationError("Unchanged sources need a refresh window")
    desired = _index_by_id(items, tenant_scope)
    if not dry_run:
        await backend.create_index()
    owned = await backend.list_documents(
        odata_filter=build_corpus_filter(tenant_scope, corpus_id),
        select=["id", "sourceId", "sourceVersion"],
    )
    existing = {str(document["id"]) for document in owned}
    kept = _kept_ids(owned, unchanged)
    await _refuse_foreign_collisions(backend, set(desired), corpus_id)

    wanted = set(desired) | kept
    added = sorted(set(desired) - existing)
    removed = sorted(existing - wanted)
    _refuse_unsafe_deletion(
        existing=len(existing),
        removed=len(removed),
        desired=len(wanted),
        allow_large_delete=allow_large_delete,
        allow_empty=allow_empty,
    )
    if not dry_run:
        # Write first: if the delete fails, the index keeps extra evidence, never less.
        await backend.upload(
            [evidence_to_document(item, corpus_id=corpus_id) for item in desired.values()]
        )
        if kept and refresh is not None:
            await backend.merge_documents(_refresh_documents(sorted(kept), refresh))
        if removed:
            await backend.delete_documents(removed)
    return {
        "action": "sync",
        "dryRun": dry_run,
        "desired": len(wanted),
        "added": len(added),
        "refreshed": len(wanted) - len(added),
        "merged": len(kept),
        "removed": len(removed),
    }


def _kept_ids(owned: Sequence[Mapping[str, Any]], unchanged: Mapping[str, str]) -> set[str]:
    kept = {
        str(document["id"])
        for document in owned
        if unchanged.get(str(document.get("sourceId"))) == document.get("sourceVersion")
    }
    held = {str(document.get("sourceId")) for document in owned if str(document["id"]) in kept}
    missing = set(unchanged) - held
    if missing:
        raise InputValidationError(
            "The index no longer holds an unchanged source at its recorded version; "
            "rerun the sync so it is extracted again"
        )
    return kept


def _refresh_documents(ids: Sequence[str], refresh: Refresh) -> list[dict[str, Any]]:
    labels = list(validate_authorization_labels(refresh.authorization_labels))
    return [
        {
            "id": document_id,
            "ingestedAt": refresh.ingested_at.astimezone(UTC).isoformat(),
            "freshUntil": refresh.fresh_until.astimezone(UTC).isoformat(),
            LABELS_FIELD: labels,
        }
        for document_id in ids
    ]


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
