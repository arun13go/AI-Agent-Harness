"""Prove lexical scoring is deterministic, bounded, relevance-ordered, and never scope-dependent."""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.retrieval.lexical import IdfCoverageScorer, tokenize
from agent_harness.core.errors import RetrievalError
from agent_harness.core.types import RetrievedItem
from tests.conformance.fixtures import IDENTITY_A, NOW, TENANT_B, make_evidence

ROOT = Path(__file__).parents[2]
LABELS = ("group:ops", "group:readers")


def doc(
    name: str, text: str, *, authorization_labels: tuple[str, ...] | None = None
) -> RetrievedItem:
    item = make_evidence(name, content=text, score=0.0)
    if authorization_labels is None:
        return item
    return replace(item, authorization_labels=authorization_labels)


CORPUS = [
    doc("restart", "Restart the API service: drain traffic, then restart the process."),
    doc("billing", "Invoices are issued monthly and payable within thirty days."),
    doc("both", "Restart the billing worker after rotating the invoices queue."),
]


def provider(items: list[RetrievedItem]) -> InMemoryRetrievalProvider:
    return InMemoryRetrievalProvider(items, clock=lambda: NOW, scorer=IdfCoverageScorer())


async def search(
    items: list[RetrievedItem], query: str, *, labels: tuple[str, ...] = LABELS
) -> list[RetrievedItem]:
    found = await provider(items).retrieve(
        identity=IDENTITY_A, authorization_labels=labels, query=query, limit=100
    )
    return list(found)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("How do I restart the API service?", {"restart", "api", "service"}),
        ("RESTART Restart restart", {"restart"}),
        (
            "Restarts, invoices; billing-worker_queue",
            {"restart", "invoice", "billing", "worker", "queue"},
        ),
        ("status process class analysis bus", {"status", "process", "class", "analysis", "bus"}),
        ("a I x 7 v7 ok", {"v7", "ok"}),
        ("Caf\u00e9 \uff21\uff22\uff23", {"caf\u00e9", "abc"}),
        ("the of and what which", set[str]()),
        ("", set[str]()),
    ],
)
def test_tokenization_is_normalized_and_consistent(text: str, expected: set[str]) -> None:
    assert tokenize(text) == frozenset(expected)


def test_tokenization_is_consistent_between_questions_and_evidence() -> None:
    assert tokenize("Invoices") == tokenize("invoice") == tokenize("INVOICES.")
    assert tokenize("e\u0301") == tokenize("\u00e9")  # composed and decomposed forms agree


def test_scores_are_bounded_aligned_with_candidates_and_rank_by_coverage() -> None:
    scores = IdfCoverageScorer().score("restart the API service", CORPUS)

    assert len(scores) == len(CORPUS)
    assert all(0.0 <= score <= 1.0 for score in scores)
    assert scores[0] == 1.0  # "restart" and "service" and "api" all present
    assert 0.0 < scores[2] < scores[0]  # only "restart" present
    assert scores[1] == 0.0


def test_rarer_question_words_weigh_more_than_common_ones() -> None:
    candidates = [
        doc("common-only", "restart procedure notes"),
        doc("rare-only", "zebra procedure notes"),
        doc("filler-1", "restart handbook entry"),
        doc("filler-2", "restart handbook chapter"),
    ]

    common, rare = IdfCoverageScorer().score("restart zebra", candidates)[:2]

    assert rare > common


def test_a_question_word_found_nowhere_caps_every_score_below_one() -> None:
    scores = IdfCoverageScorer().score("restart quokka", CORPUS)

    assert max(scores) < 1.0
    assert max(IdfCoverageScorer().score("restart", CORPUS)) == 1.0


@pytest.mark.parametrize("query", ["", "   ", "the of and", "?!"])
def test_questions_without_content_words_score_zero(query: str) -> None:
    assert IdfCoverageScorer().score(query, CORPUS) == (0.0, 0.0, 0.0)


def test_no_candidates_yields_no_scores() -> None:
    assert IdfCoverageScorer().score("restart", []) == ()


def test_adding_query_words_to_a_document_never_lowers_its_score() -> None:
    rng = random.Random(11)
    vocabulary = [f"term{n}" for n in range(12)]
    scorer = IdfCoverageScorer()
    for _ in range(40):
        query = " ".join(rng.sample(vocabulary, 4))
        base_words = rng.sample(vocabulary, rng.randint(0, 6))
        extra = rng.choice(vocabulary)
        others = [doc(f"other{n}", " ".join(rng.sample(vocabulary, 3))) for n in range(3)]
        before = scorer.score(query, [doc("target", " ".join(base_words)), *others])[0]
        after = scorer.score(query, [doc("target", " ".join([*base_words, extra])), *others])[0]
        assert after >= before - 1e-9


def test_scores_are_identical_across_processes_and_hash_seeds() -> None:
    # Many terms with widely varying weights make float summation order observable.
    script = (
        "import random\n"
        "from agent_harness.adapters.retrieval.lexical import IdfCoverageScorer\n"
        "from tests.conformance.fixtures import make_evidence\n"
        "rng = random.Random(5)\n"
        "vocabulary = [f'term{n}' for n in range(60)]\n"
        "docs = [\n"
        "    make_evidence(f'd{n}', content=' '.join(rng.sample(vocabulary, rng.randint(2, 25))))\n"
        "    for n in range(80)\n"
        "]\n"
        "print(IdfCoverageScorer().score(' '.join(vocabulary[:50]), docs))\n"
    )
    outputs: set[str] = set()
    for seed in ("1", "2", "99"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.add(result.stdout.strip())

    assert len(outputs) == 1


@pytest.mark.asyncio
async def test_provider_ranks_by_relevance_and_drops_irrelevant_evidence() -> None:
    found = await search(CORPUS, "restart the API service")

    assert [item.item_id for item in found] == ["restart", "both"]
    assert found[0].score == 1.0 > found[1].score > 0.0
    assert all(item.source_label == "untrusted-retrieved-content" for item in found)
    assert all(item.retrieved_at == NOW for item in found)
    assert await search(CORPUS, "kubernetes autoscaling") == []


@pytest.mark.asyncio
async def test_equal_scores_are_ordered_by_item_id() -> None:
    twins = [doc("zulu", "restart notes"), doc("alpha", "restart notes")]

    assert [item.item_id for item in await search(twins, "restart")] == ["alpha", "zulu"]


@pytest.mark.asyncio
async def test_provider_still_filters_tenant_and_labels_before_scoring() -> None:
    items = [
        *CORPUS,
        replace(doc("foreign", "restart the API service"), tenant_scope=TENANT_B),
        doc("admins-only", "restart the API service", authorization_labels=("group:admins",)),
    ]

    found = await search(items, "restart the API service")

    assert "foreign" not in {item.item_id for item in found}
    assert "admins-only" not in {item.item_id for item in found}
    assert await search(items, "restart the API service", labels=()) == []


@pytest.mark.asyncio
async def test_text_the_caller_cannot_see_never_changes_a_visible_score() -> None:
    visible = await search(CORPUS, "restart billing")
    # Flood other tenants and other labels with the question's own words, which would shift
    # document frequencies if they leaked into scoring.
    hidden = [
        replace(doc(f"foreign-{n}", "restart billing restart billing"), tenant_scope=TENANT_B)
        for n in range(20)
    ] + [
        doc(f"restricted-{n}", "restart billing", authorization_labels=("group:admins",))
        for n in range(20)
    ]

    after = await search([*CORPUS, *hidden], "restart billing")

    assert [(item.item_id, item.score) for item in after] == [
        (item.item_id, item.score) for item in visible
    ]


@pytest.mark.asyncio
async def test_provider_without_a_scorer_keeps_preset_scores() -> None:
    items = [make_evidence("a", score=0.2), make_evidence("b", score=0.8)]
    plain = InMemoryRetrievalProvider(items, clock=lambda: NOW)

    found = await plain.retrieve(
        identity=IDENTITY_A, authorization_labels=LABELS, query="anything", limit=10
    )

    assert [(item.item_id, item.score) for item in found] == [("b", 0.8), ("a", 0.2)]


class Misbehaving:
    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def score(self, query: str, candidates: object) -> list[float]:
        del query, candidates
        return self._scores


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scores",
    [[0.5], [0.5, 0.5, 0.5, 0.5], [1.5, 0.5, 0.5], [-0.1, 0.5, 0.5], [math.nan, 0.5, 0.5]],
    ids=["too-few", "too-many", "above-one", "negative", "nan"],
)
async def test_invalid_scorer_output_is_rejected(scores: list[float]) -> None:
    bad = InMemoryRetrievalProvider(CORPUS, clock=lambda: NOW, scorer=Misbehaving(scores))

    with pytest.raises(RetrievalError, match="invalid scores"):
        await bad.retrieve(
            identity=IDENTITY_A, authorization_labels=LABELS, query="restart", limit=10
        )
