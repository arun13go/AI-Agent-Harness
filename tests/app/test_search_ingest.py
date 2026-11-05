"""Prove corpus synchronization is exact, scoped, safely ordered, and careful with deletion."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_harness.adapters.retrieval.azure_search import (
    CORPUS_FIELD,
    LABELS_FIELD,
    TENANT_FIELD,
    build_corpus_filter,
    build_id_filter,
    evidence_to_document,
    validate_corpus_id,
)
from agent_harness.adapters.retrieval.markdown_corpus import load_markdown_corpus
from agent_harness.app import cli
from agent_harness.app.cli import main
from agent_harness.app.search_ingest import sync_corpus
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import RetrievedItem
from tests.conformance.fixtures import TENANT_A, TENANT_B, make_evidence

T0 = datetime(2026, 11, 1, tzinfo=UTC)
T1 = T0 + timedelta(days=7)
_CORPUS_FILTER = re.compile(r"^tenantScope eq '([^']*)' and corpusId eq '([^']*)'$")
_ID_FILTER = re.compile(r"^search\.in\(id, '([^']*)', ','\)$")


class FakeIndex:
    """Keep documents in memory and record every operation, in order."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.operations: list[str] = []
        self.fail_on: str | None = None

    def seed(self, item: RetrievedItem, corpus_id: str | None) -> None:
        self.documents[item.item_id] = evidence_to_document(item, corpus_id=corpus_id)

    async def create_index(self) -> None:
        self.operations.append("create_index")

    async def upload(self, documents: Sequence[Mapping[str, Any]]) -> int:
        self.operations.append("upload")
        if self.fail_on == "upload":
            raise RuntimeError("upload failed")
        for document in documents:
            self.documents[str(document["id"])] = dict(document)
        return len(documents)

    async def delete_documents(self, document_ids: Sequence[str]) -> int:
        self.operations.append("delete")
        if self.fail_on == "delete":
            raise RuntimeError("delete failed")
        for document_id in document_ids:
            self.documents.pop(document_id, None)
        return len(document_ids)

    async def list_documents(
        self, *, odata_filter: str, select: Sequence[str]
    ) -> list[dict[str, Any]]:
        self.operations.append("list")
        if match := _CORPUS_FILTER.fullmatch(odata_filter):
            tenant, corpus = match.groups()
            chosen = [
                d
                for d in self.documents.values()
                if d[TENANT_FIELD] == tenant and d[CORPUS_FIELD] == corpus
            ]
        elif match := _ID_FILTER.fullmatch(odata_filter):
            wanted = set(match.group(1).split(","))
            chosen = [d for d in self.documents.values() if d["id"] in wanted]
        else:
            raise AssertionError(f"fake cannot interpret filter: {odata_filter}")
        return [{name: document.get(name) for name in select} for document in chosen]


def write_corpus(root: Path, files: Mapping[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def chunks(root: Path, *, at: datetime = T0, tenant: str = TENANT_A, labels: Sequence[str] = ()):
    return load_markdown_corpus(
        root, tenant_scope=tenant, authorization_labels=labels, ingested_at=at
    )


FILES = {
    "alpha.md": "# Alpha\n\nAlpha facts.\n\n## Detail\n\nMore alpha detail.\n",
    "beta.md": "# Beta\n\nBeta facts.\n",
}


async def sync(index: FakeIndex, items: Sequence[RetrievedItem], **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("tenant_scope", TENANT_A)
    kwargs.setdefault("corpus_id", "handbook")
    return await sync_corpus(index, items=items, **kwargs)  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_first_sync_writes_every_chunk_with_its_scope(tmp_path: Path) -> None:
    index = FakeIndex()
    items = chunks(write_corpus(tmp_path / "c", FILES), labels=("group:ops",))

    report = await sync(index, items)

    assert report == {
        "action": "sync",
        "dryRun": False,
        "desired": len(items),
        "added": len(items),
        "refreshed": 0,
        "removed": 0,
    }
    assert set(index.documents) == {item.item_id for item in items}
    for document in index.documents.values():
        assert document[TENANT_FIELD] == TENANT_A
        assert document[CORPUS_FIELD] == "handbook"
        assert document[LABELS_FIELD] == ["group:ops"]


@pytest.mark.asyncio
async def test_resyncing_an_unchanged_folder_changes_nothing_but_freshness(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root, at=T0)
    await sync(index, first)
    before = {key: dict(value) for key, value in index.documents.items()}

    report = await sync(index, chunks(root, at=T1))

    assert (report["added"], report["removed"], report["refreshed"]) == (0, 0, len(first))
    assert set(index.documents) == set(before)
    for key, document in index.documents.items():
        assert document["freshUntil"] != before[key]["freshUntil"]
        assert {k: v for k, v in document.items() if k not in ("ingestedAt", "freshUntil")} == {
            k: v for k, v in before[key].items() if k not in ("ingestedAt", "freshUntil")
        }


@pytest.mark.asyncio
async def test_an_edited_file_replaces_only_its_own_chunks(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root)
    await sync(index, first)
    beta_before = {i.item_id for i in first if i.source.source_id == "beta.md"}
    alpha_before = {i.item_id for i in first if i.source.source_id == "alpha.md"}

    write_corpus(root, {"alpha.md": "# Alpha\n\nAlpha facts, now corrected.\n"})
    second = chunks(root)
    report = await sync(index, second)

    alpha_after = {i.item_id for i in second if i.source.source_id == "alpha.md"}
    assert alpha_after.isdisjoint(alpha_before)
    assert {i.item_id for i in second if i.source.source_id == "beta.md"} == beta_before
    assert report["removed"] == len(alpha_before)
    assert report["added"] == len(alpha_after)
    assert set(index.documents) == {i.item_id for i in second}
    assert not alpha_before & set(index.documents)


@pytest.mark.asyncio
async def test_a_removed_file_has_its_chunks_deleted(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root)
    await sync(index, first)
    (root / "alpha.md").unlink()

    second = chunks(root)
    report = await sync(index, second)

    assert report["removed"] == len([i for i in first if i.source.source_id == "alpha.md"])
    assert {d["sourceId"] for d in index.documents.values()} == {"beta.md"}


@pytest.mark.asyncio
async def test_a_changed_label_set_is_applied_to_existing_chunks(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    await sync(index, chunks(root, labels=("group:ops",)))

    report = await sync(index, chunks(root, labels=("group:ops", "group:hr")))

    assert (report["added"], report["removed"]) == (0, 0)
    assert index.documents
    assert all(
        sorted(d[LABELS_FIELD]) == ["group:hr", "group:ops"] for d in index.documents.values()
    )


@pytest.mark.asyncio
async def test_a_sync_never_touches_another_corpus_or_tenant(tmp_path: Path) -> None:
    index = FakeIndex()
    other_corpus = make_evidence("other-corpus-chunk")
    other_tenant = make_evidence("other-tenant-chunk")
    index.seed(other_corpus, "runbooks")
    index.seed(replace(other_tenant, tenant_scope=TENANT_B), "handbook")
    root = write_corpus(tmp_path / "c", FILES)
    await sync(index, chunks(root))
    (root / "alpha.md").unlink()

    await sync(index, chunks(root))

    assert "other-corpus-chunk" in index.documents
    assert "other-tenant-chunk" in index.documents


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["runbooks", None])
async def test_a_chunk_owned_by_another_corpus_is_never_overwritten(
    tmp_path: Path, owner: str | None
) -> None:
    index = FakeIndex()
    items = chunks(write_corpus(tmp_path / "c", FILES))
    index.seed(items[0], owner)

    with pytest.raises(InputValidationError, match="another corpus"):
        await sync(index, items)

    assert "upload" not in index.operations and "delete" not in index.operations
    assert index.documents[items[0].item_id][CORPUS_FIELD] == owner


@pytest.mark.asyncio
async def test_the_collision_check_covers_every_batch_of_chunks(tmp_path: Path) -> None:
    files = {f"doc-{n:03d}.md": f"# Doc {n}\n\nText {n}.\n" for n in range(120)}
    items = chunks(write_corpus(tmp_path / "c", files))
    index = FakeIndex()
    index.seed(items[-1], "runbooks")

    with pytest.raises(InputValidationError, match="another corpus"):
        await sync(index, items)

    assert index.operations.count("list") >= 3


@pytest.mark.asyncio
async def test_writes_precede_deletions_so_a_failure_never_loses_evidence(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root)
    await sync(index, first)
    write_corpus(root, {"alpha.md": "# Alpha\n\nAlpha facts, now corrected.\n"})
    second = chunks(root)
    index.operations.clear()
    index.fail_on = "delete"

    with pytest.raises(RuntimeError):
        await sync(index, second)

    assert index.operations.index("upload") < index.operations.index("delete")
    assert {i.item_id for i in second} <= set(index.documents)
    assert {i.item_id for i in first} <= set(index.documents)


@pytest.mark.asyncio
async def test_a_failed_upload_deletes_nothing(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    first = chunks(root)
    await sync(index, first)
    (root / "alpha.md").unlink()
    index.fail_on = "upload"
    index.operations.clear()

    with pytest.raises(RuntimeError):
        await sync(index, chunks(root))

    assert "delete" not in index.operations
    assert {i.item_id for i in first} <= set(index.documents)


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_same_counts_and_changes_nothing(tmp_path: Path) -> None:
    index = FakeIndex()
    root = write_corpus(tmp_path / "c", FILES)
    await sync(index, chunks(root))
    write_corpus(root, {"alpha.md": "# Alpha\n\nChanged.\n", "gamma.md": "# Gamma\n\nNew.\n"})
    items = chunks(root)
    snapshot = json.dumps(index.documents, sort_keys=True)
    index.operations.clear()

    preview = await sync(index, items, dry_run=True)

    assert preview["dryRun"] is True
    assert set(index.operations) == {"list"}
    assert json.dumps(index.documents, sort_keys=True) == snapshot
    real = await sync(index, items)
    assert {k: v for k, v in preview.items() if k != "dryRun"} == {
        k: v for k, v in real.items() if k != "dryRun"
    }


@pytest.mark.asyncio
async def test_emptying_a_corpus_needs_explicit_confirmation(tmp_path: Path) -> None:
    index = FakeIndex()
    await sync(index, chunks(write_corpus(tmp_path / "c", FILES)))
    count = len(index.documents)

    with pytest.raises(InputValidationError, match="every chunk"):
        await sync(index, [])
    assert len(index.documents) == count

    report = await sync(index, [], allow_empty=True)

    assert report["removed"] == count and index.documents == {}


@pytest.mark.asyncio
async def test_syncing_nothing_into_an_empty_corpus_is_a_no_op() -> None:
    index = FakeIndex()

    report = await sync(index, [])

    assert (report["added"], report["removed"], report["desired"]) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_large_deletion_needs_confirmation_but_a_small_corpus_does_not(
    tmp_path: Path,
) -> None:
    files = {f"doc-{n:02d}.md": f"# Doc {n}\n\nText {n}.\n" for n in range(12)}
    root = write_corpus(tmp_path / "c", files)
    index = FakeIndex()
    await sync(index, chunks(root))
    for name in list(files)[:8]:
        (root / name).unlink()
    remaining = chunks(root)

    with pytest.raises(InputValidationError, match="8 of 12"):
        await sync(index, remaining)
    assert len(index.documents) == 12

    report = await sync(index, remaining, allow_large_delete=True)
    assert report["removed"] == 8

    small_root = write_corpus(tmp_path / "s", {"a.md": "# A\n\nA.\n", "b.md": "# B\n\nB.\n"})
    small = FakeIndex()
    await sync(small, chunks(small_root), corpus_id="tiny")
    (small_root / "a.md").unlink()
    assert (await sync(small, chunks(small_root), corpus_id="tiny"))["removed"] == 1


@pytest.mark.asyncio
async def test_half_of_a_corpus_may_be_removed_without_confirmation(tmp_path: Path) -> None:
    files = {f"doc-{n:02d}.md": f"# Doc {n}\n\nText {n}.\n" for n in range(12)}
    root = write_corpus(tmp_path / "c", files)
    index = FakeIndex()
    await sync(index, chunks(root))
    for name in list(files)[:6]:
        (root / name).unlink()

    report = await sync(index, chunks(root))

    assert report["removed"] == 6


@pytest.mark.asyncio
async def test_chunks_from_another_tenant_or_with_duplicate_ids_are_rejected(
    tmp_path: Path,
) -> None:
    items = chunks(write_corpus(tmp_path / "c", FILES))
    foreign = chunks(write_corpus(tmp_path / "f", FILES), tenant=TENANT_B)
    index = FakeIndex()

    with pytest.raises(InputValidationError, match="tenant"):
        await sync(index, [*items, foreign[0]])
    with pytest.raises(InputValidationError, match="unique"):
        await sync(index, [*items, items[0]])
    assert index.operations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant", "corpus"),
    [
        (TENANT_A, "Upper"),
        (TENANT_A, ""),
        (TENANT_A, "has space"),
        (TENANT_A, "-leading"),
        (TENANT_A, "a" * 65),
        (TENANT_A, "x' or corpusId ne 'y"),
        ("short", "handbook"),
        ("tenant-0001' or 1 eq 1 or '", "handbook"),
    ],
)
async def test_invalid_tenants_and_corpus_ids_are_rejected_before_any_request(
    tenant: str, corpus: str
) -> None:
    index = FakeIndex()

    with pytest.raises(InputValidationError):
        await sync(index, [], tenant_scope=tenant, corpus_id=corpus)

    assert index.operations == []


def test_filters_are_built_from_validated_values() -> None:
    assert build_corpus_filter(TENANT_A, "handbook") == (
        "tenantScope eq 'tenant-0001' and corpusId eq 'handbook'"
    )
    assert build_id_filter(["a1", "b_2"]) == "search.in(id, 'a1,b_2', ',')"
    for bad in ([], ["a,b"], ["a'b"], ["a b"], [""], ["a", "b/c"]):
        with pytest.raises(InputValidationError):
            build_id_filter(bad)
    assert validate_corpus_id("handbook-2") == "handbook-2"


@pytest.mark.asyncio
async def test_a_report_contains_counts_only(tmp_path: Path) -> None:
    index = FakeIndex()

    report = await sync(index, chunks(write_corpus(tmp_path / "c", FILES)))

    assert set(report) == {"action", "dryRun", "desired", "added", "refreshed", "removed"}
    assert all(isinstance(value, int | bool | str) for value in report.values())


# --- Command line ------------------------------------------------------------------------------


def run_cli(
    capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    code = main(
        [
            "search-index",
            "--endpoint",
            "https://srch-test.search.windows.net",
            "--index",
            "harness-chunks",
            *args,
        ]
    )
    captured = capsys.readouterr()
    return (
        code,
        json.loads(captured.out) if captured.out else {},
        json.loads(captured.err) if captured.err else {},
    )


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> FakeIndex:
    fake = FakeIndex()

    def build(*, endpoint: str, index_name: str) -> FakeIndex:
        del endpoint, index_name
        return fake

    monkeypatch.setattr(cli, "build_backend", build)
    return fake


def test_cli_syncs_a_folder_and_supports_a_dry_run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex
) -> None:
    root = write_corpus(tmp_path / "c", FILES)
    common = [
        "--action",
        "sync",
        "--corpus",
        str(root),
        "--tenant",
        TENANT_A,
        "--corpus-id",
        "handbook",
        "--corpus-label",
        "group:ops",
    ]

    code, preview, _ = run_cli(capsys, *common, "--dry-run")
    assert code == 0 and preview["dryRun"] is True and index.documents == {}

    code, out, _ = run_cli(capsys, *common)
    assert code == 0 and out["added"] == out["desired"] > 0
    assert all(d[LABELS_FIELD] == ["group:ops"] for d in index.documents.values())


@pytest.mark.parametrize("missing", ["--corpus", "--tenant", "--corpus-id"])
def test_cli_sync_requires_its_arguments(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex, missing: str
) -> None:
    arguments = {
        "--corpus": str(write_corpus(tmp_path / "c", FILES)),
        "--tenant": TENANT_A,
        "--corpus-id": "handbook",
    }
    del arguments[missing]
    flat = [part for pair in arguments.items() for part in pair]

    code, out, err = run_cli(capsys, "--action", "sync", *flat)

    assert code == 2 and out == {}
    assert err["error"] == "configuration_error" and "required for sync" in err["message"]
    assert index.operations == []


def test_cli_refuses_a_large_deletion_unless_allowed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex
) -> None:
    files = {f"doc-{n:02d}.md": f"# Doc {n}\n\nText {n}.\n" for n in range(12)}
    root = write_corpus(tmp_path / "c", files)
    base = ["--action", "sync", "--tenant", TENANT_A, "--corpus-id", "handbook"]
    code, _, _ = run_cli(capsys, *base, "--corpus", str(root))
    assert code == 0 and len(index.documents) == 12
    shrunk = write_corpus(tmp_path / "shrunk", {"doc-00.md": files["doc-00.md"]})

    code, out, err = run_cli(capsys, *base, "--corpus", str(shrunk))

    assert code == 2 and out == {} and "11 of 12" in err["message"]
    assert len(index.documents) == 12

    code, out, _ = run_cli(capsys, *base, "--corpus", str(shrunk), "--allow-large-delete")

    assert code == 0 and out["removed"] == 11 and len(index.documents) == 1


def test_cli_reports_a_refused_sync_as_a_typed_error_without_writing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex
) -> None:
    items = chunks(write_corpus(tmp_path / "c", FILES))
    index.seed(items[0], "runbooks")

    code, out, err = run_cli(
        capsys,
        "--action",
        "sync",
        "--corpus",
        str(tmp_path / "c"),
        "--tenant",
        TENANT_A,
        "--corpus-id",
        "handbook",
    )

    assert code == 2 and out == {} and err["error"] == "input_validation_error"
    assert "upload" not in index.operations
