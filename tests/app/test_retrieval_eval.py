"""Prove the retrieval evaluation computes metrics correctly and its guards fail properly."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_harness.app.cli import main
from agent_harness.app.retrieval_eval import EvaluationReport, evaluate_retrieval
from agent_harness.core.configuration import load_harness_config
from agent_harness.core.errors import ConfigurationError, InputValidationError

ROOT = Path(__file__).parents[2]
DATASET = ROOT / "evals" / "grounded-relevance"
PROFILE = ROOT / "config" / "profiles" / "grounded-assistant-local.yaml"
CONFIG_SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"


def question(qid: str, category: str, text: str, route: str, *sources: str) -> dict[str, object]:
    return {
        "id": qid,
        "category": category,
        "question": text,
        "expected_route": route,
        "expected_sources": list(sources),
    }


def dataset_yaml(*questions: dict[str, object]) -> str:
    return yaml.safe_dump(
        {"version": 1, "name": "tiny", "questions": list(questions)}, sort_keys=False
    )


def thresholds_yaml(*rules: dict[str, object]) -> str:
    return yaml.safe_dump({"version": 1, "rules": list(rules)}, sort_keys=False)


QUESTIONS = dataset_yaml(
    question("a1", "exact", "When does the alpha service restart?", "answer", "alpha.md"),
    question("a2", "paraphrase", "How are beta bills settled?", "answer", "beta.md"),
    question("u1", "unanswerable", "What is the capital of France?", "abstain"),
    # Deliberately wrong expectation: the corpus answers this, so it counts as a false answer.
    question("u2", "near-miss", "When does the alpha service restart?", "abstain"),
)


def make_dataset(root: Path, questions: str = QUESTIONS, thresholds: str | None = None) -> Path:
    (root / "corpus").mkdir(parents=True)
    (root / "corpus" / "alpha.md").write_text(
        "# Alpha\n\nThe alpha service restarts nightly at midnight.\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "corpus" / "beta.md").write_text(
        "# Beta\n\nInvoices for beta customers are paid monthly.\n", encoding="utf-8", newline="\n"
    )
    (root / "questions.yaml").write_text(questions, encoding="utf-8", newline="\n")
    if thresholds is not None:
        (root / "thresholds.yaml").write_text(thresholds, encoding="utf-8", newline="\n")
    return root


async def evaluate(root: Path, **overrides: Any) -> EvaluationReport:
    config = load_harness_config(PROFILE, CONFIG_SCHEMA)
    return await evaluate_retrieval(
        dataset_dir=root, config=config, event_schema=EVENT_SCHEMA, **overrides
    )


@pytest.mark.asyncio
async def test_metrics_match_hand_computed_values(tmp_path: Path) -> None:
    report = await evaluate(make_dataset(tmp_path / "ds"))

    by_id = {result.id: result for result in report.results}
    assert by_id["a1"].answered and by_id["a1"].cited_sources == ("alpha.md",)
    assert not by_id["a2"].answered and by_id["a2"].reasons == ("weak",)
    assert not by_id["u1"].answered and by_id["u1"].reasons == ("missing",)
    assert by_id["u2"].answered  # the expectation is deliberately wrong to count a false answer
    assert report.metrics["all"] == {
        "questions": 4,
        "route_accuracy": 0.5,  # a1 and u1 correct; a2 missed; u2 answered when it should not
        "answer_rate": 0.5,  # one of two answerable questions answered
        "source_recall": 0.5,
        "citation_precision": 1.0,
        "false_answer_rate": 0.5,  # u2 answered, u1 abstained
    }
    assert report.metrics["exact"]["answer_rate"] == 1.0
    assert report.metrics["paraphrase"]["answer_rate"] == 0.0
    assert report.metrics["near-miss"]["false_answer_rate"] == 1.0
    assert report.metrics["exact"]["false_answer_rate"] is None  # no unanswerable questions
    assert report.passed is None and report.failures == ()


@pytest.mark.asyncio
async def test_citation_precision_penalizes_extra_cited_sources(tmp_path: Path) -> None:
    questions = dataset_yaml(question("p1", "exact", "alpha beta", "answer", "alpha.md"))
    report = await evaluate(make_dataset(tmp_path / "ds", questions), minimum_score=0.3)

    result = report.results[0]
    assert result.cited_sources == ("alpha.md", "beta.md")
    assert report.metrics["all"]["citation_precision"] == 0.5
    assert report.metrics["all"]["source_recall"] == 1.0


@pytest.mark.asyncio
async def test_source_recall_requires_every_expected_source(tmp_path: Path) -> None:
    questions = dataset_yaml(
        question("r1", "multi-source", "alpha service restart", "answer", "alpha.md", "beta.md"),
        question("r2", "exact", "alpha service restart", "answer", "alpha.md"),
    )
    report = await evaluate(make_dataset(tmp_path / "ds", questions))

    assert [r.cited_sources for r in report.results] == [("alpha.md",), ("alpha.md",)]
    assert report.metrics["multi-source"]["source_recall"] == 0.0  # beta.md was never cited
    assert report.metrics["multi-source"]["answer_rate"] == 1.0
    assert report.metrics["exact"]["source_recall"] == 1.0
    assert report.metrics["all"]["source_recall"] == 0.5


@pytest.mark.asyncio
async def test_thresholds_report_each_failed_rule(tmp_path: Path) -> None:
    thresholds = """\
version: 1
rules:
  - {metric: false_answer_rate, category: all, maximum: 0.0}
  - {metric: answer_rate, category: exact, minimum: 1.0}
  - {metric: answer_rate, category: paraphrase, minimum: 0.5}
  - {metric: source_recall, category: unanswerable, minimum: 0.5}
  - {metric: answer_rate, category: no-such-category, minimum: 0.1}
"""
    report = await evaluate(make_dataset(tmp_path / "ds", thresholds=thresholds))

    assert report.passed is False
    assert report.failures == (
        "false_answer_rate[all] = 0.5 is above the maximum 0.0",
        "answer_rate[paraphrase] = 0.0 is below the minimum 0.5",
        "source_recall[unanswerable] is undefined: the category has no applicable questions",
        "answer_rate[no-such-category] is undefined: the category has no applicable questions",
    )


@pytest.mark.asyncio
async def test_thresholds_pass_when_every_rule_holds_and_can_be_skipped(tmp_path: Path) -> None:
    thresholds = """\
version: 1
rules:
  - {metric: answer_rate, category: exact, minimum: 1.0}
  - {metric: citation_precision, category: all, minimum: 1.0}
"""
    root = make_dataset(tmp_path / "ds", thresholds=thresholds)

    assert (await evaluate(root)).passed is True
    skipped = await evaluate(root, apply_thresholds=False)
    assert skipped.passed is None and skipped.failures == ()


@pytest.mark.asyncio
async def test_the_same_inputs_give_identical_reports(tmp_path: Path) -> None:
    root = make_dataset(tmp_path / "ds")

    assert (await evaluate(root)).to_json() == (await evaluate(root)).to_json()


@pytest.mark.asyncio
async def test_a_question_expecting_a_missing_source_is_rejected(tmp_path: Path) -> None:
    questions = dataset_yaml(question("x1", "exact", "alpha", "answer", "gamma.md"))

    with pytest.raises(InputValidationError, match=r"x1.*gamma\.md"):
        await evaluate(make_dataset(tmp_path / "ds", questions))


def _entry(**changes: object) -> dict[str, object]:
    return {**question("a", "exact", "q", "abstain"), **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entries",
    [
        [_entry(expected_route="answer", expected_sources=["alpha.md"])] * 2,
        [_entry(expected_route="answer")],
        [_entry(expected_sources=["alpha.md"])],
        [_entry(category="all")],
        [_entry(id="A")],
        [_entry(expected_route="maybe")],
        [_entry(extra=1)],
        [_entry(question="")],
    ],
    ids=[
        "duplicate-ids",
        "answer-without-sources",
        "abstain-with-sources",
        "reserved-category",
        "bad-id",
        "bad-route",
        "unknown-field",
        "empty-question",
    ],
)
async def test_invalid_datasets_are_rejected(
    tmp_path: Path, entries: list[dict[str, object]]
) -> None:
    with pytest.raises(ConfigurationError, match=r"questions\.yaml"):
        await evaluate(make_dataset(tmp_path / "ds", dataset_yaml(*entries)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thresholds",
    [
        thresholds_yaml({"metric": "answer_rate", "category": "all"}),
        thresholds_yaml(
            {"metric": "answer_rate", "category": "all", "minimum": 0.1, "maximum": 0.9}
        ),
        thresholds_yaml({"metric": "nonsense", "category": "all", "minimum": 0.1}),
        thresholds_yaml({"metric": "answer_rate", "category": "all", "minimum": 1.5}),
        thresholds_yaml(),
    ],
    ids=["no-bound", "two-bounds", "unknown-metric", "out-of-range", "no-rules"],
)
async def test_invalid_thresholds_are_rejected(tmp_path: Path, thresholds: str) -> None:
    with pytest.raises(ConfigurationError, match=r"thresholds\.yaml"):
        await evaluate(make_dataset(tmp_path / "ds", thresholds=thresholds))


@pytest.mark.asyncio
async def test_missing_files_are_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"questions\.yaml"):
        await evaluate(tmp_path)


@pytest.mark.asyncio
async def test_the_repository_baseline_meets_its_guards_and_never_answers_the_unanswerable() -> (
    None
):
    report = await evaluate(DATASET)

    assert report.passed is True, report.failures
    assert len(report.results) == 33
    assert report.metrics["all"]["false_answer_rate"] == 0.0
    assert report.metrics["near-miss"]["false_answer_rate"] == 0.0
    assert {r.category for r in report.results} == {
        "exact",
        "paraphrase",
        "multi-source",
        "short",
        "unanswerable",
        "near-miss",
    }


@pytest.mark.asyncio
async def test_the_guards_catch_a_threshold_loose_enough_to_answer_near_misses() -> None:
    report = await evaluate(DATASET, minimum_score=0.1)

    assert report.passed is False
    assert any(failure.startswith("false_answer_rate[near-miss]") for failure in report.failures)


def run_cli(
    capsys: pytest.CaptureFixture[str], *extra: str, profile: Path = PROFILE
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    code = main(
        [
            "eval-retrieval",
            "--profile",
            str(profile),
            "--config-schema",
            str(CONFIG_SCHEMA),
            "--event-schema",
            str(EVENT_SCHEMA),
            *extra,
        ]
    )
    captured = capsys.readouterr()
    out: dict[str, Any] = json.loads(captured.out) if captured.out else {}
    err: dict[str, Any] = json.loads(captured.err) if captured.err else {}
    return code, out, err


def test_cli_reports_metrics_and_exits_zero_when_guards_hold(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run_cli(capsys, "--dataset", str(DATASET))

    assert code == 0 and out["passed"] is True and out["failures"] == []
    assert out["metrics"]["all"]["false_answer_rate"] == 0.0
    assert len(out["results"]) == 33
    assert "capital of France" not in json.dumps(out)  # reports ids, not question text


def test_cli_exits_one_and_still_prints_the_report_when_a_guard_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run_cli(capsys, "--dataset", str(DATASET), "--minimum-score", "0.1")

    assert code == 1 and out["passed"] is False and out["failures"]


def test_cli_report_only_skips_the_guards(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run_cli(
        capsys, "--dataset", str(DATASET), "--minimum-score", "0.1", "--report-only"
    )

    assert code == 0 and out["passed"] is None


@pytest.mark.parametrize(
    ("extra", "profile", "error"),
    [
        (("--minimum-score", "1.5"), PROFILE, "configuration_error"),
        (("--dataset", "absent-directory"), PROFILE, "configuration_error"),
        ((), ROOT / "config" / "profiles" / "assistant.yaml", "configuration_error"),
        ((), ROOT / "config" / "profiles" / "grounded-assistant.yaml", "configuration_error"),
    ],
    ids=["bad-score", "missing-dataset", "wrong-profile", "azure-profile"],
)
def test_cli_reports_typed_errors_without_output(
    capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], profile: Path, error: str
) -> None:
    code, out, err = run_cli(capsys, *extra, profile=profile)

    assert code == 2 and out == {} and err["error"] == error
