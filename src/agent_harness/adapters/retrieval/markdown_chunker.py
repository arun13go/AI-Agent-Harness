"""Split Markdown into deterministic, structure-aware evidence chunks."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

from agent_harness.core.errors import InputValidationError
from agent_harness.core.governance import (
    validate_authorization_labels,
    validate_source_reference,
    validate_tenant_scope,
)
from agent_harness.core.types import RetrievedItem, SourceReference

MAXIMUM_SOURCE_CHARACTERS = 1_000_000
MAXIMUM_CHUNKS_PER_SOURCE = 10_000

_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TABLE_ROW = re.compile(r"^ {0,3}\|")
_SLUG_UNSAFE = re.compile(r"[^\w.-]+")


@dataclass(frozen=True, slots=True)
class _Block:
    kind: str
    lines: tuple[str, ...]


class MarkdownStructureChunker:
    """Keep headings, tables, and code blocks intact and derive stable chunk identifiers.

    The chunk path is the document path, a ``#``, then the heading slugs joined by ``/``. Repeats
    of the same path gain ``~2``, ``~3`` and so on. Content is carried as data and never alters
    identity, paths, labels, or routing.
    """

    def __init__(self, *, maximum_chunk_characters: int = 2000) -> None:
        if not 200 <= maximum_chunk_characters <= 16_000:
            raise InputValidationError("Maximum chunk size must be between 200 and 16000")
        self._limit = maximum_chunk_characters

    def chunk(
        self,
        *,
        source: SourceReference,
        content: str,
        tenant_scope: str,
        authorization_labels: Sequence[str],
    ) -> tuple[RetrievedItem, ...]:
        """Return scoped chunks in document order, or none for blank content."""

        validate_tenant_scope(tenant_scope)
        labels = validate_authorization_labels(authorization_labels)
        validate_source_reference(source)
        if len(content) > MAXIMUM_SOURCE_CHARACTERS:
            raise InputValidationError("Source content exceeds the chunking size limit")
        if "\x00" in content:
            raise InputValidationError("Source content must not contain NUL characters")

        normalized = unicodedata.normalize("NFC", content).replace("\r\n", "\n").replace("\r", "\n")
        texts_by_path = self._sections(normalized)
        if len(texts_by_path) > MAXIMUM_CHUNKS_PER_SOURCE:
            raise InputValidationError("Source content produces too many chunks")

        seen: dict[str, int] = {}
        items: list[RetrievedItem] = []
        for slug_path, text in texts_by_path:
            base = f"{source.structural_path}#{slug_path}"
            occurrence = seen.get(base, 0) + 1
            seen[base] = occurrence
            path = base if occurrence == 1 else f"{base}~{occurrence}"
            items.append(
                RetrievedItem(
                    item_id=_chunk_id(tenant_scope, source, path),
                    content=text,
                    score=0.0,
                    source=replace(source, structural_path=path),
                    tenant_scope=tenant_scope,
                    authorization_labels=labels,
                )
            )
        return tuple(items)

    def _sections(self, text: str) -> list[tuple[str, str]]:
        """Return ``(heading path, chunk text)`` pairs; sections without a body are skipped."""

        results: list[tuple[str, str]] = []
        stack: list[tuple[int, str]] = []
        heading: _Block | None = None
        body: list[_Block] = []
        path = "preamble"

        def flush() -> None:
            if not body:
                return
            blocks = ([heading] if heading else []) + body
            for piece in self._pack(blocks):
                results.append((path, piece))

        for block in _blocks(text.split("\n")):
            if block.kind != "heading":
                body.append(block)
                continue
            flush()
            match = _HEADING.match(block.lines[0])
            assert match is not None
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, _slug(match.group(2))))
            path = "/".join(slug for _, slug in stack)
            heading, body = block, []
        flush()
        return results

    def _pack(self, blocks: Sequence[_Block]) -> list[str]:
        """Pack whole blocks into chunks no larger than the limit, splitting only oversized ones."""

        pieces: list[str] = []
        for position, block in enumerate(blocks):
            limit = self._limit
            if position == 1 and blocks[0].kind == "heading":
                # Reserve room so the heading shares a chunk with the first piece of its body.
                reserved = len("\n".join(blocks[0].lines)) + 2
                if self._limit - reserved >= 100:
                    limit = self._limit - reserved
            pieces.extend(self._split(block, limit))
        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for piece in pieces:
            added = len(piece) + (2 if current else 0)
            if current and size + added > self._limit:
                chunks.append("\n\n".join(current))
                current, size, added = [], 0, len(piece)
            current.append(piece)
            size += added
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    @staticmethod
    def _split(block: _Block, limit: int) -> list[str]:
        """Split an oversized block at line boundaries, repeating a table's header rows."""

        text = "\n".join(block.lines)
        if len(text) <= limit:
            return [text]
        lines = list(block.lines)
        prefix = ""
        if block.kind == "table" and len(lines) > 2:
            header = "\n".join(lines[:2])
            if len(header) * 2 <= limit:
                prefix, lines = header + "\n", lines[2:]
        budget = limit - len(prefix)
        pieces: list[str] = []
        current: list[str] = []
        size = 0
        for line in lines:
            for start in range(0, max(len(line), 1), budget):
                segment = line[start : start + budget]
                added = len(segment) + (1 if current else 0)
                if current and size + added > budget:
                    pieces.append(prefix + "\n".join(current))
                    current, size, added = [], 0, len(segment)
                current.append(segment)
                size += added
        if current:
            pieces.append(prefix + "\n".join(current))
        return pieces


def _blocks(lines: Sequence[str]) -> list[_Block]:
    blocks: list[_Block] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            end = index + 1
            while end < len(lines) and not _closes_fence(lines[end], marker):
                end += 1
            blocks.append(_Block("code", tuple(lines[index : end + 1])))
            index = end + 1
        elif _HEADING.match(line):
            blocks.append(_Block("heading", (line,)))
            index += 1
        elif _TABLE_ROW.match(line):
            end = index
            while end < len(lines) and _TABLE_ROW.match(lines[end]):
                end += 1
            blocks.append(_Block("table", tuple(lines[index:end])))
            index = end
        else:
            end = index
            while (
                end < len(lines)
                and lines[end].strip()
                and not (
                    _FENCE.match(lines[end])
                    or _HEADING.match(lines[end])
                    or _TABLE_ROW.match(lines[end])
                )
            ):
                end += 1
            blocks.append(_Block("paragraph", tuple(lines[index:end])))
            index = end
    return blocks


def _closes_fence(line: str, marker: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= len(marker) and set(stripped) == {marker[0]}


def _slug(heading: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", heading.strip().casefold()).strip("-.")
    return slug[:64] or "section"


def _chunk_id(tenant_scope: str, source: SourceReference, path: str) -> str:
    identity = json.dumps(
        [tenant_scope, source.source_id, source.source_version, path],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "chk_" + hashlib.sha256(identity.encode("ascii")).hexdigest()[:32]
