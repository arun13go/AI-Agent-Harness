"""Define the behavior every chunker must satisfy; adapters supply only a factory."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.core.answerability import DeterministicAnswerabilityPolicy
from agent_harness.core.contracts import Chunker
from agent_harness.core.errors import InputValidationError
from agent_harness.core.types import AnswerabilityRoute, RetrievedItem, SourceReference
from tests.conformance.fixtures import (
    HANDBOOK,
    HOSTILE_HEADINGS,
    IDENTITY_A,
    IDENTITY_B,
    LABELS,
    NOW,
    TENANT_A,
    TENANT_B,
    make_source,
)

# The one heading-only section in HANDBOOK; chunkers may fold it into the path instead.
_HEADING_ONLY_LINE = "# Security"


class ChunkerConformance:
    """Subclass with a ``Test`` prefix, set the size bound, and implement ``make_chunker``."""

    maximum_chunk_characters: int

    def make_chunker(self) -> Chunker:
        raise NotImplementedError

    def chunks(
        self,
        content: str = HANDBOOK,
        *,
        tenant: str = TENANT_A,
        labels: tuple[str, ...] = LABELS,
        source_id: str = "handbook",
        version: str = "2026-11-01",
    ) -> tuple[RetrievedItem, ...]:
        return tuple(
            self.make_chunker().chunk(
                source=make_source(source_id=source_id, version=version),
                content=content,
                tenant_scope=tenant,
                authorization_labels=labels,
            )
        )

    def test_output_is_deterministic_across_instances_and_line_endings(self) -> None:
        first = self.chunks()
        assert first
        assert self.chunks() == first
        assert self.chunks(HANDBOOK.replace("\n", "\r\n")) == first
        assert self.chunks(HANDBOOK.replace("\n", "\r")) == first

    def test_equivalent_label_sets_produce_identical_chunks(self) -> None:
        reference = self.chunks(labels=("group:ops", "group:readers"))
        assert self.chunks(labels=("group:readers", "group:ops", "group:ops")) == reference

    def test_identity_is_scoped_by_tenant_source_and_version(self) -> None:
        base = {item.item_id for item in self.chunks()}
        for variant in (
            self.chunks(tenant=TENANT_B),
            self.chunks(source_id="other-handbook"),
            self.chunks(version="2026-11-02"),
        ):
            assert base.isdisjoint({item.item_id for item in variant})

    def test_labels_and_content_do_not_change_identity(self) -> None:
        # Re-ingesting under changed labels or edited text must overwrite, never leave a stale copy.
        base = [item.item_id for item in self.chunks()]
        assert [item.item_id for item in self.chunks(labels=("group:other",))] == base
        edited = HANDBOOK.replace(
            "Operational overview paragraph.", "Operational overview, revised."
        )
        assert edited != HANDBOOK
        assert [item.item_id for item in self.chunks(edited)] == base

    def test_ids_and_paths_are_unique_and_extend_the_document_path(self) -> None:
        items = self.chunks()
        assert len({item.item_id for item in items}) == len(items)
        assert len({item.source.structural_path for item in items}) == len(items)
        assert all(item.source.structural_path.startswith("/docs/handbook.md") for item in items)
        assert all(item.item_id.startswith("chk_") for item in items)

    def test_chunks_carry_scope_lineage_freshness_and_untrusted_label(self) -> None:
        for item in self.chunks():
            assert item.tenant_scope == TENANT_A
            assert item.authorization_labels == ("group:ops", "group:readers")
            assert item.source.source_id == "handbook"
            assert item.source.source_version == "2026-11-01"
            assert item.source.ingested_at == make_source().ingested_at
            assert item.source.fresh_until == make_source().fresh_until
            assert item.source_label == "untrusted-retrieved-content"
            assert item.evidence_flags == ()
            assert item.retrieved_at is None
            assert item.content.strip()

    def test_chunks_are_bounded_and_preserve_body_text(self) -> None:
        items = self.chunks()
        assert all(len(item.content) <= self.maximum_chunk_characters for item in items)
        combined = "\n".join(item.content for item in items)
        for line in HANDBOOK.splitlines():
            if line.strip() and line != _HEADING_ONLY_LINE:
                assert line in combined

    def test_hostile_text_stays_inert_evidence(self) -> None:
        injected = [item for item in self.chunks() if "SYSTEM: reveal" in item.content]
        assert injected
        for item in injected:
            assert item.tenant_scope == TENANT_A
            assert item.authorization_labels == ("group:ops", "group:readers")
            assert item.source_label == "untrusted-retrieved-content"
            assert item.evidence_flags == ()
            assert "reveal" not in item.item_id

    def test_hostile_headings_cannot_forge_paths_or_cross_tenants(self) -> None:
        document_path = make_source().structural_path
        for item in self.chunks(HOSTILE_HEADINGS):
            suffix = item.source.structural_path.removeprefix(document_path + "#")
            assert suffix and "#" not in suffix
            assert ".." not in suffix.split("/")
            assert TENANT_B not in item.item_id

    def test_blank_content_yields_no_chunks(self) -> None:
        assert self.chunks("") == ()
        assert self.chunks("  \n\n\t\n") == ()

    @pytest.mark.parametrize(
        "case",
        [
            "short-tenant",
            "bad-label",
            "too-many-labels",
            "hash-in-path",
            "naive-ingested-at",
            "naive-fresh-until",
            "control-in-id",
            "nul-content",
        ],
    )
    def test_invalid_input_is_rejected_with_a_typed_error(self, case: str) -> None:
        source: SourceReference = make_source()
        content = "# Title\n\nBody.\n"
        tenant = TENANT_A
        labels: tuple[str, ...] = LABELS
        if case == "short-tenant":
            tenant = "t1"
        elif case == "bad-label":
            labels = ("bad label",)
        elif case == "too-many-labels":
            labels = tuple(f"label-{n}" for n in range(65))
        elif case == "hash-in-path":
            source = replace(source, structural_path="/docs/a#b")
        elif case == "naive-ingested-at":
            source = replace(source, ingested_at=datetime(2026, 11, 2))
        elif case == "naive-fresh-until":
            source = replace(source, fresh_until=datetime(2026, 12, 2))
        elif case == "control-in-id":
            source = replace(source, source_id="bad\nid")
        elif case == "nul-content":
            content = "Body\x00"
        with pytest.raises(InputValidationError):
            self.make_chunker().chunk(
                source=source,
                content=content,
                tenant_scope=tenant,
                authorization_labels=labels,
            )

    @pytest.mark.asyncio
    async def test_chunks_flow_through_retrieval_without_crossing_tenants_or_labels(self) -> None:
        tenant_a = self.chunks(tenant=TENANT_A, labels=("group:readers",))
        tenant_b = self.chunks(tenant=TENANT_B, labels=("group:readers",))
        provider = InMemoryRetrievalProvider([*tenant_a, *tenant_b], clock=lambda: NOW)

        for identity, expected in ((IDENTITY_A, tenant_a), (IDENTITY_B, tenant_b)):
            found = await provider.retrieve(
                identity=identity,
                authorization_labels=("group:readers",),
                query="restart",
                limit=100,
            )
            assert {item.item_id for item in found} == {item.item_id for item in expected}
            assert all(item.tenant_scope == identity.tenant_scope for item in found)

        unlabeled = await provider.retrieve(
            identity=IDENTITY_A, authorization_labels=(), query="restart", limit=100
        )
        assert unlabeled == ()

        found = await provider.retrieve(
            identity=IDENTITY_A,
            authorization_labels=("group:readers",),
            query="restart",
            limit=100,
        )
        decision = DeterministicAnswerabilityPolicy(
            minimum_score=0.0,
            insufficient_evidence_route=AnswerabilityRoute.ABSTAIN,
            maximum_retrieval_attempts=0,
        ).assess(
            evidence=found,
            now=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
            retrieval_attempt=0,
        )
        assert decision.route is AnswerabilityRoute.ANSWER
        assert {citation.structural_path for citation in decision.citations} == {
            item.source.structural_path for item in tenant_a
        }
