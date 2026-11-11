"""Measure grounded-path relevance deterministically over a versioned question set."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.retrieval.lexical import IdfCoverageScorer
from agent_harness.adapters.retrieval.markdown_corpus import load_markdown_corpus
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.app.grounded_executor import create_grounded_executor
from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.contracts import RetrievalProvider
from agent_harness.core.errors import ConfigurationError, InputValidationError
from agent_harness.core.types import (
    AnswerabilityRoute,
    IdentityScope,
    Message,
    ModelRequest,
    ModelResponse,
    RequestContext,
    RetrievedItem,
    TokenUsage,
)

EVALUATION_TENANT = "tenant-eval-0001"
EVALUATION_SUBJECT = "subject-eval-0001"
_EVALUATION_TIME = datetime(2026, 1, 1, tzinfo=UTC)
_METRICS = (
    "route_accuracy",
    "answer_rate",
    "source_recall",
    "citation_precision",
    "false_answer_rate",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationQuestion(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    category: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    question: str = Field(min_length=1, max_length=2048)
    expected_route: Literal["answer", "abstain"]
    expected_sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _sources_match_route(self) -> Self:
        if self.expected_route == "answer" and not self.expected_sources:
            raise ValueError("answer questions need expected_sources")
        if self.expected_route == "abstain" and self.expected_sources:
            raise ValueError("abstain questions must not list expected_sources")
        if len(set(self.expected_sources)) != len(self.expected_sources):
            raise ValueError("expected_sources must not repeat")
        return self


class EvaluationDataset(_Strict):
    version: Literal[1]
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    questions: tuple[EvaluationQuestion, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _ids_unique_and_categories_not_reserved(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique")
        if any(question.category == "all" for question in self.questions):
            raise ValueError("'all' is a reserved category")
        return self


class ThresholdRule(_Strict):
    metric: Literal[
        "route_accuracy", "answer_rate", "source_recall", "citation_precision", "false_answer_rate"
    ]
    category: str = "all"
    minimum: float | None = Field(default=None, ge=0, le=1)
    maximum: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _exactly_one_bound(self) -> Self:
        if (self.minimum is None) == (self.maximum is None):
            raise ValueError("a rule needs exactly one of minimum or maximum")
        return self


class Thresholds(_Strict):
    version: Literal[1]
    rules: tuple[ThresholdRule, ...] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class QuestionResult:
    id: str
    category: str
    expected_route: str
    route: str
    reasons: tuple[str, ...]
    expected_sources: tuple[str, ...]
    cited_sources: tuple[str, ...]

    @property
    def answered(self) -> bool:
        return self.route == AnswerabilityRoute.ANSWER.value


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    dataset: str
    minimum_score: float
    chunk_count: int
    metrics: dict[str, dict[str, float | int | None]]
    results: tuple[QuestionResult, ...]
    failures: tuple[str, ...]
    passed: bool | None

    def to_json(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "minimumScore": self.minimum_score,
            "chunkCount": self.chunk_count,
            "passed": self.passed,
            "failures": list(self.failures),
            "metrics": self.metrics,
            "results": [
                {
                    "id": result.id,
                    "category": result.category,
                    "expectedRoute": result.expected_route,
                    "route": result.route,
                    "reasons": list(result.reasons),
                    "expectedSources": list(result.expected_sources),
                    "citedSources": list(result.cited_sources),
                }
                for result in self.results
            ],
        }


class _NoModel:
    """Stand in for the model: the evaluation measures retrieval and routing only."""

    async def run(
        self,
        *,
        authenticated_identity: IdentityScope,
        request: ModelRequest,
        run_already_started: bool = False,
    ) -> ModelResponse:
        del authenticated_identity, request, run_already_started
        return ModelResponse(
            message=Message("assistant", "(model not called)", "model-output"),
            tool_calls=(),
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            model_id="evaluation-stub",
            finish_reason="stop",
        )


def _load_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"Cannot read evaluation file '{path.name}'") from error


def _validate[ModelT: BaseModel](model: type[ModelT], value: object, name: str) -> ModelT:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ConfigurationError(f"Invalid evaluation file '{name}': {error}") from error


def load_evaluation_corpus(dataset_dir: Path) -> Sequence[RetrievedItem]:
    """Load the dataset's Markdown corpus as evidence for the fixed evaluation tenant."""

    return load_markdown_corpus(
        dataset_dir / "corpus",
        tenant_scope=EVALUATION_TENANT,
        authorization_labels=(),
        ingested_at=_EVALUATION_TIME,
        fresh_for=timedelta(days=36500),
    )


def load_evaluation_dataset(dataset_dir: Path) -> EvaluationDataset:
    """Load and validate a dataset's ``questions.yaml``."""

    return _validate(
        EvaluationDataset, _load_yaml(dataset_dir / "questions.yaml"), "questions.yaml"
    )


async def evaluate_retrieval(
    *,
    dataset_dir: Path,
    config: HarnessConfig,
    event_schema: Path,
    minimum_score: float = 0.5,
    apply_thresholds: bool = True,
    retrieval: RetrievalProvider | None = None,
) -> EvaluationReport:
    """Run every question through the composed grounded path and score the outcomes.

    By default retrieval is the in-memory lexical provider. Pass another provider to score it on
    the same questions; its index must already hold the evaluation corpus for the same tenant.
    """

    dataset = load_evaluation_dataset(dataset_dir)
    thresholds: Thresholds | None = None
    thresholds_path = dataset_dir / "thresholds.yaml"
    if apply_thresholds and thresholds_path.is_file():
        thresholds = _validate(Thresholds, _load_yaml(thresholds_path), "thresholds.yaml")

    corpus = load_evaluation_corpus(dataset_dir)
    known_sources = {item.source.source_id for item in corpus}
    for question in dataset.questions:
        missing = sorted(set(question.expected_sources) - known_sources)
        if missing:
            raise InputValidationError(
                f"Question '{question.id}' expects sources missing from the corpus: {missing}"
            )

    audit = InMemoryAuditSink(event_schema)
    executor = create_grounded_executor(
        config=config,
        retrieval=retrieval
        or InMemoryRetrievalProvider(
            corpus, clock=lambda: _EVALUATION_TIME, scorer=IdfCoverageScorer()
        ),
        runtime=_NoModel(),
        audit_sink=audit,
        minimum_evidence_score=minimum_score,
        clock=lambda: _EVALUATION_TIME,
    )
    identity = IdentityScope(EVALUATION_TENANT, EVALUATION_SUBJECT)
    results: list[QuestionResult] = []
    for question in dataset.questions:
        digest = hashlib.sha256(question.id.encode("utf-8")).hexdigest()
        response = await executor.run(
            authenticated_identity=identity,
            context=RequestContext(
                identity=identity,
                run_id=f"run-eval-{question.id}",
                correlation_id=f"correlation-eval-{question.id}",
                trace_id=digest[:32],
                deadline=_EVALUATION_TIME + timedelta(hours=1),
            ),
            question=question.question,
            authorization_labels=(),
        )
        results.append(
            QuestionResult(
                id=question.id,
                category=question.category,
                expected_route=question.expected_route,
                route=response.route.value,
                reasons=tuple(reason.value for reason in response.reasons),
                expected_sources=question.expected_sources,
                cited_sources=tuple(sorted({c.source_id for c in response.citations})),
            )
        )

    metrics = {"all": _metrics(results)}
    for category in sorted({result.category for result in results}):
        metrics[category] = _metrics([r for r in results if r.category == category])
    failures = _check(thresholds, metrics) if thresholds is not None else ()
    return EvaluationReport(
        dataset=dataset.name,
        minimum_score=minimum_score,
        chunk_count=len(corpus),
        metrics=metrics,
        results=tuple(results),
        failures=failures,
        passed=None if thresholds is None else not failures,
    )


def _metrics(results: Sequence[QuestionResult]) -> dict[str, float | int | None]:
    answerable = [r for r in results if r.expected_route == "answer"]
    unanswerable = [r for r in results if r.expected_route == "abstain"]
    answered = [r for r in answerable if r.answered]

    def ratio(numerator: float, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    precision = [
        len(set(r.cited_sources) & set(r.expected_sources)) / len(r.cited_sources)
        for r in answered
        if r.cited_sources
    ]
    return {
        "questions": len(results),
        "route_accuracy": ratio(
            sum(r.answered == (r.expected_route == "answer") for r in results), len(results)
        ),
        "answer_rate": ratio(len(answered), len(answerable)),
        "source_recall": ratio(
            sum(set(r.expected_sources) <= set(r.cited_sources) for r in answered), len(answerable)
        ),
        "citation_precision": ratio(sum(precision), len(answered)),
        "false_answer_rate": ratio(sum(r.answered for r in unanswerable), len(unanswerable)),
    }


def _check(
    thresholds: Thresholds, metrics: dict[str, dict[str, float | int | None]]
) -> tuple[str, ...]:
    failures: list[str] = []
    for rule in thresholds.rules:
        value = metrics.get(rule.category, {}).get(rule.metric)
        label = f"{rule.metric}[{rule.category}]"
        if value is None:
            failures.append(f"{label} is undefined: the category has no applicable questions")
        elif rule.minimum is not None and value < rule.minimum:
            failures.append(f"{label} = {value} is below the minimum {rule.minimum}")
        elif rule.maximum is not None and value > rule.maximum:
            failures.append(f"{label} = {value} is above the maximum {rule.maximum}")
    return tuple(failures)
