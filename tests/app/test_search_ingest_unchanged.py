"""Prove sources kept without re-extraction are refreshed in place and never lose evidence."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_harness.adapters.retrieval.azure_search import CORPUS_FIELD, LABELS_FIELD
from agent_harness.app.search_ingest import Refresh, known_source_versions, sync_corpus
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import RetrievedItem
from tests.app.test_search_ingest import (
    FILES,
    T0,
    T1,
    FakeIndex,
    chunks,
    write_corpus,
)
from tests.conformance.fixtures import TENANT_A, TENANT_B

REFRESH = Refresh(
    ingested_at=T1, fresh_until=T1 + timedelta(days=30), authorization_labels=("group:hr",)
)


def version_of(items: list[RetrievedItem] | tuple[RetrievedItem, ...], source: str) -> str:
    return next(i.source.source_version for i in items if i.source.source_id == source)


async def seed(tmp_path: Path) -> tuple[FakeIndex, Path, tuple[RetrievedItem, ...]]:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root, at=T0, labels=("group:ops",))
    await sync_corpus(index, items=first, tenant_scope=TENANT_A, corpus_id="handbook")
    return index, root, first


@pytest.mark.asyncio
async def test_kept_chunks_are_refreshed_in_place_without_being_rewritten(tmp_path: Path) -> None:
    index, root, first = await seed(tmp_path)
    alpha_ids = {i.item_id for i in first if i.source.source_id == "alpha.md"}
    beta_only = [i for i in chunks(root, at=T1) if i.source.source_id == "beta.md"]
    index.operations.clear()

    report = await sync_corpus(
        index,
        items=beta_only,
        tenant_scope=TENANT_A,
        corpus_id="handbook",
        unchanged_sources={"alpha.md": version_of(first, "alpha.md")},
        refresh=REFRESH,
    )

    assert report["merged"] == len(alpha_ids)
    assert (report["added"], report["removed"]) == (0, 0)
    assert report["desired"] == len(alpha_ids) + len(beta_only)
    assert index.operations.index("upload") < index.operations.index("merge")
    for chunk_id in alpha_ids:
        document = index.documents[chunk_id]
        assert document["ingestedAt"] == T1.isoformat()
        assert document["freshUntil"] == (T1 + timedelta(days=30)).isoformat()
        assert document[LABELS_FIELD] == ["group:hr"]
        assert document[CORPUS_FIELD] == "handbook"
        assert "alpha" in document["content"].lower()


@pytest.mark.asyncio
async def test_kept_chunks_count_toward_the_deletion_guards(tmp_path: Path) -> None:
    files = {f"doc-{n:02d}.md": f"# Doc {n}\n\nText {n}.\n" for n in range(12)}
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", files)
    first = chunks(root)
    await sync_corpus(index, items=first, tenant_scope=TENANT_A, corpus_id="handbook")
    everything = {name: version_of(first, name) for name in files}

    report = await sync_corpus(
        index,
        items=[],
        tenant_scope=TENANT_A,
        corpus_id="handbook",
        unchanged_sources=everything,
        refresh=REFRESH,
    )

    assert (report["removed"], report["merged"]) == (0, len(first))
    assert len(index.documents) == len(first)


@pytest.mark.asyncio
async def test_an_unchanged_source_missing_from_the_index_stops_the_sync(tmp_path: Path) -> None:
    index, root, _ = await seed(tmp_path)
    index.operations.clear()

    with pytest.raises(InputValidationError, match="no longer holds"):
        await sync_corpus(
            index,
            items=[i for i in chunks(root) if i.source.source_id == "beta.md"],
            tenant_scope=TENANT_A,
            corpus_id="handbook",
            unchanged_sources={"gamma.md": "0123456789abcdef"},
            refresh=REFRESH,
        )

    assert not {"upload", "merge", "delete"} & set(index.operations)


@pytest.mark.asyncio
async def test_an_unchanged_source_recorded_at_another_version_stops_the_sync(
    tmp_path: Path,
) -> None:
    index, _, first = await seed(tmp_path)
    index.operations.clear()
    before = set(index.documents)

    with pytest.raises(InputValidationError, match="no longer holds"):
        await sync_corpus(
            index,
            items=[],
            tenant_scope=TENANT_A,
            corpus_id="handbook",
            unchanged_sources={
                "alpha.md": "ffffffffffffffff",
                "beta.md": version_of(first, "beta.md"),
            },
            refresh=REFRESH,
        )

    assert set(index.documents) == before
    assert not {"upload", "merge", "delete"} & set(index.operations)


@pytest.mark.asyncio
async def test_unchanged_sources_need_a_refresh_window(tmp_path: Path) -> None:
    index, _, first = await seed(tmp_path)

    with pytest.raises(InputValidationError, match="refresh window"):
        await sync_corpus(
            index,
            items=[],
            tenant_scope=TENANT_A,
            corpus_id="handbook",
            unchanged_sources={"alpha.md": version_of(first, "alpha.md")},
        )


@pytest.mark.asyncio
async def test_a_dry_run_reports_merges_but_changes_nothing(tmp_path: Path) -> None:
    index, root, first = await seed(tmp_path)
    snapshot = {key: dict(value) for key, value in index.documents.items()}
    index.operations.clear()

    report = await sync_corpus(
        index,
        items=[i for i in chunks(root, at=T1) if i.source.source_id == "beta.md"],
        tenant_scope=TENANT_A,
        corpus_id="handbook",
        unchanged_sources={"alpha.md": version_of(first, "alpha.md")},
        refresh=REFRESH,
        dry_run=True,
    )

    assert isinstance(report["merged"], int) and report["merged"] > 0
    assert index.documents == snapshot
    assert set(index.operations) == {"list"}


@pytest.mark.asyncio
async def test_a_failed_merge_deletes_nothing(tmp_path: Path) -> None:
    index, root, first = await seed(tmp_path)
    (root / "beta.md").unlink()
    index.fail_on = "merge"
    index.operations.clear()

    with pytest.raises(RuntimeError):
        await sync_corpus(
            index,
            items=[],
            tenant_scope=TENANT_A,
            corpus_id="handbook",
            unchanged_sources={"alpha.md": version_of(first, "alpha.md")},
            refresh=REFRESH,
            allow_large_delete=True,
        )

    assert "delete" not in index.operations
    assert {i.item_id for i in first} <= set(index.documents)


@pytest.mark.asyncio
async def test_another_corpus_or_tenant_is_never_kept_or_merged(tmp_path: Path) -> None:
    index, _, first = await seed(tmp_path)
    foreign = chunks(write_corpus(tmp_path / "f", FILES), tenant=TENANT_B)
    await sync_corpus(index, items=foreign, tenant_scope=TENANT_B, corpus_id="handbook")
    other_before = {i.item_id: dict(index.documents[i.item_id]) for i in foreign}

    await sync_corpus(
        index,
        items=[],
        tenant_scope=TENANT_A,
        corpus_id="handbook",
        unchanged_sources={name: version_of(first, name) for name in ("alpha.md", "beta.md")},
        refresh=REFRESH,
    )

    assert {key: index.documents[key] for key in other_before} == other_before


@pytest.mark.asyncio
async def test_known_versions_report_one_version_per_source(tmp_path: Path) -> None:
    index, _, first = await seed(tmp_path)

    known = await known_source_versions(index, tenant_scope=TENANT_A, corpus_id="handbook")

    assert known == {
        "alpha.md": version_of(first, "alpha.md"),
        "beta.md": version_of(first, "beta.md"),
    }


@pytest.mark.asyncio
async def test_a_source_with_mixed_versions_is_left_out_so_it_is_extracted_again(
    tmp_path: Path,
) -> None:
    index, _, first = await seed(tmp_path)
    alpha_ids = sorted(i.item_id for i in first if i.source.source_id == "alpha.md")
    index.documents[alpha_ids[0]]["sourceVersion"] = "different-version"
    assert len(alpha_ids) > 1

    known = await known_source_versions(index, tenant_scope=TENANT_A, corpus_id="handbook")

    assert "alpha.md" not in known and "beta.md" in known


@pytest.mark.asyncio
async def test_known_versions_ignore_documents_without_usable_fields(tmp_path: Path) -> None:
    index, _, _ = await seed(tmp_path)
    broken: dict[str, Any] = next(iter(index.documents.values()))
    broken["sourceVersion"] = None

    known = await known_source_versions(index, tenant_scope=TENANT_A, corpus_id="handbook")

    assert isinstance(known, dict)
