"""Prove `search-index --action sync --extract-documents` indexes documents safely and cheaply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_harness.adapters.extraction.factory import ENDPOINT_ENV, create_document_extractor
from agent_harness.app import cli
from agent_harness.app.cli import main
from agent_harness.core.errors import ConfigurationError, ExtractionError, InputValidationError
from tests.app.test_search_ingest import FakeIndex
from tests.conformance.test_document_corpus import DOCX, PDF, FakeExtractor, write
from tests.conformance.test_document_intelligence import Credential

TENANT = "tenant-0001"


@pytest.fixture
def index(monkeypatch: pytest.MonkeyPatch) -> FakeIndex:
    fake = FakeIndex()

    def build(*, endpoint: str, index_name: str) -> FakeIndex:
        del endpoint, index_name
        return fake

    monkeypatch.setattr(cli, "build_backend", build)
    return fake


@pytest.fixture
def extractor(monkeypatch: pytest.MonkeyPatch) -> FakeExtractor:
    fake = FakeExtractor(pages=3)

    def create(environ: object) -> FakeExtractor:
        del environ
        return fake

    monkeypatch.setattr(cli, "create_document_extractor", create)
    return fake


def run(
    capsys: pytest.CaptureFixture[str], folder: Path, *extra: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    code = main(
        [
            "search-index",
            "--endpoint",
            "https://srch-test.search.windows.net",
            "--index",
            "harness-chunks",
            "--action",
            "sync",
            "--corpus",
            str(folder),
            "--tenant",
            TENANT,
            "--corpus-id",
            "handbook",
            *extra,
        ]
    )
    captured = capsys.readouterr()
    return (
        code,
        json.loads(captured.out) if captured.out else {},
        json.loads(captured.err) if captured.err else {},
    )


def test_documents_without_the_flag_are_refused_rather_than_skipped(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex
) -> None:
    folder = write(tmp_path, {"notes.md": "# N\n\nText.\n", "a.pdf": PDF})

    code, out, err = run(capsys, folder)

    assert code == 2 and out == {}
    assert err["error"] == "configuration_error" and "--extract-documents" in err["message"]
    assert index.operations == []


def test_a_markdown_only_folder_still_syncs_without_the_flag(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, index: FakeIndex
) -> None:
    code, out, _ = run(capsys, write(tmp_path, {"notes.md": "# N\n\nText.\n"}))

    assert code == 0 and out["added"] > 0 and "extractedPages" not in out


def test_markdown_pdf_and_word_are_indexed_and_the_page_cost_is_reported(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"notes.md": "# N\n\nText.\n", "a.pdf": PDF, "b.docx": DOCX})

    code, out, _ = run(capsys, folder, "--extract-documents", "--corpus-label", "group:ops")

    assert code == 0
    assert (out["extractedFiles"], out["extractedPages"]) == (2, 6)
    assert {d["sourceId"] for d in index.documents.values()} == {"notes.md", "a.pdf", "b.docx"}
    assert all(d["authorizationLabels"] == ["group:ops"] for d in index.documents.values())
    assert all(d["corpusId"] == "handbook" for d in index.documents.values())


def test_unchanged_documents_are_not_extracted_or_billed_again(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF, "b.docx": DOCX})
    run(capsys, folder, "--extract-documents")
    first = {key: dict(value) for key, value in index.documents.items()}
    extractor.calls.clear()

    code, out, _ = run(capsys, folder, "--extract-documents")

    assert code == 0 and extractor.calls == []
    assert (out["extractedFiles"], out["extractedPages"], out["added"], out["removed"]) == (
        0,
        0,
        0,
        0,
    )
    assert out["merged"] == len(first) and set(index.documents) == set(first)
    assert all(index.documents[k]["freshUntil"] >= first[k]["freshUntil"] for k in first)
    assert all(index.documents[k]["ingestedAt"] > first[k]["ingestedAt"] for k in first)


def test_a_changed_document_is_extracted_again_and_its_old_chunks_removed(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF, "b.docx": DOCX})
    run(capsys, folder, "--extract-documents")
    before = set(index.documents)
    extractor.calls.clear()
    write(tmp_path, {"a.pdf": PDF + b" edited"})

    code, out, _ = run(capsys, folder, "--extract-documents")

    assert code == 0 and [name for name, _ in extractor.calls] == ["a.pdf"]
    assert out["extractedFiles"] == 1 and out["added"] == 1 and out["removed"] == 1
    assert len(set(index.documents) - before) == 1 and len(before - set(index.documents)) == 1


def test_labels_given_on_a_resync_reach_unchanged_documents_without_extracting_them(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF})
    run(capsys, folder, "--extract-documents", "--corpus-label", "group:ops")
    extractor.calls.clear()

    code, out, _ = run(capsys, folder, "--extract-documents", "--corpus-label", "group:hr")

    assert code == 0 and extractor.calls == [] and out["merged"] > 0
    assert all(d["authorizationLabels"] == ["group:hr"] for d in index.documents.values())


def test_a_removed_document_has_its_chunks_deleted(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF, "b.docx": DOCX})
    run(capsys, folder, "--extract-documents")
    (tmp_path / "a.pdf").unlink()

    code, out, _ = run(capsys, folder, "--extract-documents")

    assert code == 0 and out["removed"] == 1
    assert {d["sourceId"] for d in index.documents.values()} == {"b.docx"}


def test_a_dry_run_extracts_new_files_but_writes_nothing(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF})

    code, out, _ = run(capsys, folder, "--extract-documents", "--dry-run")

    assert code == 0 and out["dryRun"] is True and out["extractedPages"] == 3
    assert index.documents == {}
    assert not {"create_index", "upload", "merge", "delete"} & set(index.operations)


def test_a_legacy_doc_file_fails_the_sync_before_any_write(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF, "old.doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"})

    code, out, err = run(capsys, folder, "--extract-documents")

    assert code == 2 and out == {} and err["error"] == "input_validation_error"
    assert "old.doc" in err["message"]
    assert extractor.calls == [] and "upload" not in index.operations


def test_an_extraction_failure_leaves_the_index_untouched(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    extractor: FakeExtractor,
) -> None:
    folder = write(tmp_path, {"a.pdf": PDF})
    run(capsys, folder, "--extract-documents")
    snapshot = json.dumps(index.documents, sort_keys=True)
    write(tmp_path, {"a.pdf": PDF + b" edited"})
    extractor.fail_with = ExtractionError("service unavailable")
    index.operations.clear()

    code, out, err = run(capsys, folder, "--extract-documents")

    assert code == 2 and out == {} and err["error"] == "extraction_error"
    assert json.dumps(index.documents, sort_keys=True) == snapshot
    assert not {"upload", "merge", "delete"} & set(index.operations)


def test_a_missing_endpoint_variable_fails_before_the_index_is_touched(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    index: FakeIndex,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENDPOINT_ENV, raising=False)

    code, out, err = run(capsys, write(tmp_path, {"a.pdf": PDF}), "--extract-documents")

    assert code == 2 and out == {}
    assert f"'{ENDPOINT_ENV}' is not set" in err["message"]
    assert index.operations == []


# --- Factory -----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_the_factory_requires_the_endpoint_variable(value: str | None) -> None:
    environ = {} if value is None else {ENDPOINT_ENV: value}

    with pytest.raises(ConfigurationError, match=f"'{ENDPOINT_ENV}' is not set"):
        create_document_extractor(environ, credential=Credential())


def test_the_factory_rejects_a_hostile_endpoint_before_requesting_a_token() -> None:
    credential = Credential()

    with pytest.raises(InputValidationError) as raised:
        create_document_extractor({ENDPOINT_ENV: "https://evil.example"}, credential=credential)

    assert "evil.example" not in str(raised.value)
    assert credential.scopes == []


def test_the_factory_defaults_to_the_free_tier_file_limit() -> None:
    extractor = create_document_extractor(
        {ENDPOINT_ENV: "https://di-test.cognitiveservices.azure.com"}, credential=Credential()
    )

    assert extractor._max_file_bytes == 4 * 1024 * 1024  # pyright: ignore[reportPrivateUsage]


def test_the_factory_builds_an_extractor_for_a_resource_endpoint() -> None:
    extractor = create_document_extractor(
        {ENDPOINT_ENV: "https://di-test.cognitiveservices.azure.com"},
        credential=Credential(),
        max_file_bytes=1000,
    )

    assert extractor._max_file_bytes == 1000  # pyright: ignore[reportPrivateUsage]
