"""Load a directory of Markdown files as scoped, versioned evidence for local use."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from agent_harness.adapters.retrieval.markdown_chunker import MarkdownStructureChunker
from agent_harness.core.contracts import Chunker
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import RetrievedItem, SourceReference

MAXIMUM_FILES = 500
MAXIMUM_FILE_BYTES = 1_000_000
MAXIMUM_TOTAL_BYTES = 20_000_000


def load_markdown_corpus(
    root: Path,
    *,
    tenant_scope: str,
    authorization_labels: Sequence[str],
    ingested_at: datetime,
    fresh_for: timedelta = timedelta(days=30),
    chunker: Chunker | None = None,
) -> tuple[RetrievedItem, ...]:
    """Chunk every ``.md`` file under ``root`` in a stable order.

    The directory is treated as untrusted. Symbolic links, hidden files and directories (names
    starting with a dot), oversized files, undecodable files and too many files are rejected or
    skipped, never followed. A file's version is a hash of its bytes, so changed content always
    gets new chunk identifiers. Freshness is synthetic: every file is ingested at ``ingested_at``
    and fresh for ``fresh_for``.
    """

    if not root.is_dir():
        raise InputValidationError("Corpus path must be an existing directory")
    if fresh_for <= timedelta(0):
        raise InputValidationError("Corpus freshness window must be positive")
    base = root.resolve(strict=True)
    selected = _list_files(base)
    if not selected:
        raise InputValidationError("Corpus contains no Markdown files")

    effective_chunker = chunker or MarkdownStructureChunker()
    items: list[RetrievedItem] = []
    total = 0
    for relative, path in selected:
        size = path.stat().st_size
        total += size
        if size > MAXIMUM_FILE_BYTES:
            raise InputValidationError(
                f"Corpus file '{relative}' exceeds {MAXIMUM_FILE_BYTES} bytes"
            )
        if total > MAXIMUM_TOTAL_BYTES:
            raise InputValidationError(f"Corpus exceeds {MAXIMUM_TOTAL_BYTES} bytes")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InputValidationError(f"Corpus file '{relative}' is not valid UTF-8") from error
        source = SourceReference(
            source_id=relative,
            source_version=hashlib.sha256(raw).hexdigest()[:16],
            structural_path="/" + relative,
            ingested_at=ingested_at,
            fresh_until=ingested_at + fresh_for,
        )
        try:
            items.extend(
                effective_chunker.chunk(
                    source=source,
                    content=text,
                    tenant_scope=tenant_scope,
                    authorization_labels=authorization_labels,
                )
            )
        except InputValidationError as error:
            raise InputValidationError(f"Corpus file '{relative}': {error.message}") from error
    return tuple(items)


def _list_files(base: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for directory, subdirectories, filenames in os.walk(base, followlinks=False):
        parent = Path(directory)
        for name in sorted(subdirectories):
            linked = parent / name
            if linked.is_symlink():
                relative = linked.relative_to(base).as_posix()
                raise InputValidationError(f"Corpus contains a symbolic link: '{relative}'")
        subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(".md"):
                continue
            path = parent / name
            relative = path.relative_to(base).as_posix()
            if path.is_symlink():
                raise InputValidationError(f"Corpus contains a symbolic link: '{relative}'")
            found.append((relative, path))
            if len(found) > MAXIMUM_FILES:
                raise InputValidationError(f"Corpus contains more than {MAXIMUM_FILES} files")
    return found
