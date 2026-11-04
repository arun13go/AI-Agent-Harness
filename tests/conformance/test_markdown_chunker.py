"""Bind the shared chunker suite to the Markdown chunker and test its structure rules."""

from __future__ import annotations

import unicodedata

import pytest

from agent_harness.adapters.retrieval.markdown_chunker import (
    MAXIMUM_CHUNKS_PER_SOURCE,
    MAXIMUM_SOURCE_CHARACTERS,
    MarkdownStructureChunker,
)
from agent_harness.core.contracts import Chunker
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import RetrievedItem
from tests.conformance.chunker_suite import ChunkerConformance
from tests.conformance.fixtures import HANDBOOK, LABELS, TENANT_A, make_source

DOCUMENT_PATH = make_source().structural_path


def chunk(content: str, *, limit: int = 2000) -> tuple[RetrievedItem, ...]:
    return MarkdownStructureChunker(maximum_chunk_characters=limit).chunk(
        source=make_source(),
        content=content,
        tenant_scope=TENANT_A,
        authorization_labels=LABELS,
    )


def suffixes(items: tuple[RetrievedItem, ...]) -> list[str]:
    return [item.source.structural_path.removeprefix(DOCUMENT_PATH + "#") for item in items]


class TestMarkdownStructureChunker(ChunkerConformance):
    maximum_chunk_characters = 2000

    def make_chunker(self) -> Chunker:
        return MarkdownStructureChunker(maximum_chunk_characters=self.maximum_chunk_characters)


def test_sections_follow_headings_with_stable_unique_paths() -> None:
    items = chunk(HANDBOOK)

    assert suffixes(items) == [
        "preamble",
        "operations",
        "operations/restart-procedure",
        "operations/restart-procedure~2",
        "security/ignore-previous-instructions",
    ]
    assert items[0].content == "Welcome text before any heading."
    assert items[1].content == "# Operations\n\nOperational overview paragraph."


def test_code_fences_and_tables_stay_whole_inside_their_section() -> None:
    restart = chunk(HANDBOOK)[2].content

    assert "# not a heading: this is inside a code fence" in restart
    assert "| Step | Owner |\n|------|-------|\n| Drain | SRE |\n| Restart | SRE |" in restart
    assert "```bash\n# not a heading: this is inside a code fence\nsystemctl restart api\n```" in (
        restart
    )
    assert not any("not-a-heading" in path for path in suffixes(chunk(HANDBOOK)))


def test_unclosed_fence_swallows_the_rest_instead_of_opening_sections() -> None:
    items = chunk("# One\n\nText.\n\n```\ncode\n# Two\n\nmore\n")

    assert suffixes(items) == ["one"]
    assert "# Two" in items[0].content


def test_heading_levels_nest_and_pop_and_repeat_under_different_parents() -> None:
    items = chunk("# A\n\n## B\n\nx\n\n# C\n\n## B\n\ny\n\n### Deep ##\n\nz\n")

    assert suffixes(items) == ["a/b", "c/b", "c/b/deep"]


def test_oversized_table_splits_at_rows_and_repeats_its_header() -> None:
    rows = [f"| row-{n:02d} | value |" for n in range(40)]
    table = "\n".join(["| Name | Value |", "|------|-------|", *rows])
    items = chunk(f"# Data\n\n{table}\n", limit=300)

    assert len(items) > 1
    assert all(len(item.content) <= 300 for item in items)
    assert all("| Name | Value |\n|------|-------|" in item.content for item in items[1:])
    body = "\n".join(item.content for item in items)
    assert all(body.count(row) == 1 for row in rows)
    assert suffixes(items) == ["data"] + [f"data~{n}" for n in range(2, len(items) + 1)]


def test_oversized_line_is_hard_split_without_losing_text() -> None:
    long_line = "word " * 200
    items = chunk(f"# Long\n\n{long_line.strip()}\n", limit=300)

    assert len(items) > 1
    assert all(len(item.content) <= 300 for item in items)
    # The heading stays with the first piece instead of becoming an empty-looking chunk.
    assert items[0].content.startswith("# Long\n\nword")
    assert all(item.content != "# Long" for item in items)
    assert "".join(item.content.removeprefix("# Long\n\n") for item in items) == long_line.strip()


def test_small_blocks_pack_together_up_to_the_limit() -> None:
    paragraphs = "\n\n".join(f"Paragraph {n} text." for n in range(6))
    items = chunk(f"# Pack\n\n{paragraphs}\n", limit=200)

    assert len(items) == 1
    assert items[0].content.count("Paragraph") == 6


def test_unicode_normalization_makes_equivalent_text_identical() -> None:
    composed = "# Caf\u00e9\n\nBody.\n"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed

    assert chunk(composed) == chunk(decomposed)


def test_heading_slugs_cannot_contain_path_separators() -> None:
    items = chunk("# C# / a~b #hash\n\nBody.\n")

    assert len(items) == 1
    assert suffixes(items) == ["c-a-b-hash"]


def test_empty_heading_gets_a_fixed_name() -> None:
    assert suffixes(chunk("# \n\nBody.\n")) == ["section"]


def test_heading_text_named_preamble_cannot_collide_with_leading_text() -> None:
    items = chunk("Lead text.\n\n# Preamble\n\nBody.\n")

    assert suffixes(items) == ["preamble", "preamble~2"]
    assert len({item.item_id for item in items}) == 2


@pytest.mark.parametrize("limit", [199, 16_001])
def test_chunk_size_bounds_are_enforced(limit: int) -> None:
    with pytest.raises(InputValidationError):
        MarkdownStructureChunker(maximum_chunk_characters=limit)


def test_source_size_and_chunk_count_are_bounded() -> None:
    with pytest.raises(InputValidationError, match="size limit"):
        chunk("x" * (MAXIMUM_SOURCE_CHARACTERS + 1))
    many = "".join(f"# h{n}\n\nb\n\n" for n in range(MAXIMUM_CHUNKS_PER_SOURCE + 1))
    with pytest.raises(InputValidationError, match="too many chunks"):
        chunk(many)
