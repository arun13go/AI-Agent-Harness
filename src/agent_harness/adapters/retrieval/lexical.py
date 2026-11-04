"""Score evidence by how much of a question's weighted vocabulary it covers, deterministically."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence

from agent_harness.core.types import RetrievedItem

_WORD = re.compile(r"[^\W_]+")
_STOPWORDS = frozenset(
    """
    a about an and are as at be been being but by can could did do does for from had has have how
    i if in into is it its may me might must my no not of on or our shall should so than that the
    their them then there these they this those to was we were what when where which who whom
    whose why will with would you your here
    """.split()
)
_PLURAL_EXEMPT = ("ss", "us", "is")


def tokenize(text: str) -> frozenset[str]:
    """Return the distinct, normalized content words of ``text``.

    Text is NFKC-normalized and case-folded, split on non-alphanumerics, stripped of common
    English stopwords and one-character tokens, and folded for simple plurals. The same function
    is applied to questions and evidence, so only consistency matters, not linguistic accuracy.
    """

    tokens: set[str] = set()
    for word in _WORD.findall(unicodedata.normalize("NFKC", text).casefold()):
        if len(word) < 2 or word in _STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith(_PLURAL_EXEMPT):
            word = word[:-1]
        tokens.add(word)
    return frozenset(tokens)


class IdfCoverageScorer:
    """Score each candidate by the share of the question's IDF weight that its text contains.

    A score of 1.0 means every content word of the question appears in the candidate. Rarer words
    weigh more, and a question word that appears nowhere in the candidates still counts against
    every score, so questions about things the corpus does not mention score low and are routed
    to a safe answer instead of matching on filler.

    Document frequencies come only from the candidates passed in. The retrieval provider passes
    evidence the caller is authorized to see, so text in other tenants or behind other labels can
    never change a score.
    """

    def score(self, query: str, candidates: Sequence[RetrievedItem]) -> tuple[float, ...]:
        # Sorted iteration keeps float sums identical across processes and hash seeds.
        terms = sorted(tokenize(query))
        if not terms or not candidates:
            return (0.0,) * len(candidates)
        documents = [tokenize(item.content) for item in candidates]
        count = len(documents)
        weights: dict[str, float] = {}
        for term in terms:
            frequency = sum(term in document for document in documents)
            weights[term] = math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
        total = sum(weights[term] for term in terms)
        return tuple(
            round(sum(weights[term] for term in terms if term in document) / total, 6)
            for document in documents
        )
