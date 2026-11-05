"""Load a folder of Markdown, PDF and Word files as scoped, versioned evidence.

PDF and Word files go through a ``DocumentExtractor``, which turns them into Markdown that the same
chunker then splits, so chunk identity, scoping and safety rules match Markdown sources. A file's
version is a hash of its original bytes, never of the extracted text. Extraction is billed per
page, so a file whose version the caller already holds is not extracted again.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from agent_harness.adapters.retrieval.markdown_chunker import MarkdownStructureChunker
from agent_harness.adapters.retrieval.markdown_corpus import (
    MAXIMUM_FILE_BYTES,
    MAXIMUM_TOTAL_BYTES,
    list_corpus_files,
)
from agent_harness.core.contracts import Chunker, DocumentExtractor
from agent_harness.core.errors import ExtractionError, InputValidationError
from agent_harness.core.types import RetrievedItem, SourceReference

DOCUMENT_EXTENSIONS = (".pdf", ".docx")
_LEGACY_EXTENSION = ".doc"
_ALL_EXTENSIONS = (".md", *DOCUMENT_EXTENSIONS, _LEGACY_EXTENSION)


@dataclass(frozen=True, slots=True)
class LoadedCorpus:
    """Chunks for new and changed files, plus the files that did not need extracting."""

    items: tuple[RetrievedItem, ...]
    unchanged_sources: Mapping[str, str]
    extracted_files: int
    extracted_pages: int


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    raw: bytes
    version: str
    is_document: bool


def list_document_files(root: Path) -> list[str]:
    """Return the relative names of PDF and Word files under ``root``."""

    base = root.resolve(strict=True)
    return [relative for relative, _ in list_corpus_files(base, DOCUMENT_EXTENSIONS)]


def _scan(root: Path, fresh_for: timedelta) -> list[_Source]:
    """Read and hash every supported file, enforcing the folder's limits. All file I/O is here."""

    if not root.is_dir():
        raise InputValidationError("Corpus path must be an existing directory")
    if fresh_for <= timedelta(0):
        raise InputValidationError("Corpus freshness window must be positive")
    base = root.resolve(strict=True)
    selected = list_corpus_files(base, _ALL_EXTENSIONS)
    legacy = [name for name, _ in selected if name.lower().endswith(_LEGACY_EXTENSION)]
    if legacy:
        raise InputValidationError(
            f"Legacy .doc file '{legacy[0]}' is not supported; save it as .docx"
        )
    if not selected:
        raise InputValidationError("Corpus contains no Markdown, PDF or Word files")
    sources: list[_Source] = []
    total = 0
    for relative, path in selected:
        is_document = relative.lower().endswith(DOCUMENT_EXTENSIONS)
        size = path.stat().st_size
        total += size
        if not is_document and size > MAXIMUM_FILE_BYTES:
            raise InputValidationError(
                f"Corpus file '{relative}' exceeds {MAXIMUM_FILE_BYTES} bytes"
            )
        if total > MAXIMUM_TOTAL_BYTES:
            raise InputValidationError(f"Corpus exceeds {MAXIMUM_TOTAL_BYTES} bytes")
        raw = path.read_bytes()
        sources.append(_Source(relative, raw, hashlib.sha256(raw).hexdigest()[:16], is_document))
    return sources


async def load_document_corpus(
    root: Path,
    *,
    extractor: DocumentExtractor,
    tenant_scope: str,
    authorization_labels: Sequence[str],
    ingested_at: datetime,
    fresh_for: timedelta = timedelta(days=30),
    known_versions: Mapping[str, str] | None = None,
    chunker: Chunker | None = None,
) -> LoadedCorpus:
    """Chunk every Markdown, PDF and Word file under ``root`` in a stable order.

    ``known_versions`` maps a source name to the version the index already holds. A PDF or Word
    file with that exact version is reported in ``unchanged_sources`` and not extracted. Markdown
    is always chunked, since it costs nothing. A legacy ``.doc`` file is an error rather than a
    skip, because a silently skipped file would later have its chunks removed.
    """

    sources = _scan(root, fresh_for)
    effective_chunker = chunker or MarkdownStructureChunker()
    known = known_versions or {}
    items: list[RetrievedItem] = []
    unchanged: dict[str, str] = {}
    extracted_files = extracted_pages = 0
    for source in sources:
        if source.is_document and known.get(source.name) == source.version:
            unchanged[source.name] = source.version
            continue
        if source.is_document:
            try:
                extracted = await extractor.extract(content=source.raw, filename=source.name)
            except (InputValidationError, ExtractionError) as error:
                raise type(error)(f"Corpus file '{source.name}': {error.message}") from error
            text = extracted.markdown
            extracted_files += 1
            extracted_pages += extracted.page_count
        else:
            try:
                text = source.raw.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise InputValidationError(
                    f"Corpus file '{source.name}' is not valid UTF-8"
                ) from error
        reference = SourceReference(
            source_id=source.name,
            source_version=source.version,
            structural_path="/" + source.name,
            ingested_at=ingested_at,
            fresh_until=ingested_at + fresh_for,
        )
        try:
            chunks = effective_chunker.chunk(
                source=reference,
                content=text,
                tenant_scope=tenant_scope,
                authorization_labels=authorization_labels,
            )
        except InputValidationError as error:
            raise InputValidationError(f"Corpus file '{source.name}': {error.message}") from error
        if source.is_document and not chunks:
            # An empty result would otherwise read as "delete this file's evidence" to a sync.
            raise InputValidationError(f"Corpus file '{source.name}' produced no usable text")
        items.extend(chunks)
    return LoadedCorpus(
        items=tuple(items),
        unchanged_sources=unchanged,
        extracted_files=extracted_files,
        extracted_pages=extracted_pages,
    )
