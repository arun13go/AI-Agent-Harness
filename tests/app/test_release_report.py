"""Check the release report: strict inputs, fail-closed rules, two verdicts and regression."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_harness.app.cli import main
from agent_harness.app.release_report import (
    build_report,
    junit_metrics,
    load_metrics,
    load_thresholds,
    render_markdown,
    retrieval_metrics,
)
from agent_harness.core.errors import ConfigurationError

REQUIRED = ["policy", "quality", "safety", "latency", "cost", "telemetry"]
RULES: dict[str, tuple[str, str, float]] = {
    "policy": ("failures", "maximum", 0),
    "quality": ("task_adherence", "minimum", 0.8),
    "safety": ("attack_success_rate", "maximum", 0),
    "latency": ("p95_ms", "maximum", 8000),
    "cost": ("estimated_per_run", "maximum", 0.02),
    "telemetry": ("error_rate", "maximum", 0.02),
}
type Metrics = Mapping[str, Mapping[str, float]]
GOOD: dict[str, dict[str, float]] = {
    "policy": {"failures": 0},
    "quality": {"task_adherence": 0.9},
    "safety": {"attack_success_rate": 0},
    "latency": {"p95_ms": 5000},
    "cost": {"estimated_per_run": 0.01},
    "telemetry": {"error_rate": 0.0},
}


def thresholds_text(
    rules: dict[str, tuple[str, str, float]] = RULES, extra: str = "", prices: str = ""
) -> str:
    lines = ["version: 1", extra, prices, "rules:"]
    lines += [
        f"  - {{scorecard: {card}, metric: {metric}, {bound}: {value}}}"
        for card, (metric, bound, value) in rules.items()
    ]
    return "\n".join(line for line in lines if line) + "\n"


def write(tmp_path: Path, name: str, content: str | dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def report(
    tmp_path: Path,
    metrics: Metrics | None = None,
    thresholds: str | None = None,
    baseline: Metrics | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    limits = load_thresholds(write(tmp_path, "thresholds.yaml", thresholds or thresholds_text()))
    return build_report(
        limits, metrics if metrics is not None else GOOD, baseline=baseline, labels=labels or {}
    ).to_json()


def test_good_metrics_pass_both_scorecards(tmp_path: Path) -> None:
    result = report(tmp_path)

    assert result["passed"] is True
    assert result["qualityHealth"] is True and result["operationalHealth"] is True
    assert result["failures"] == []


@pytest.mark.parametrize(
    ("card", "metric", "value", "quality", "operational"),
    [
        ("policy", "failures", 1, False, True),
        ("quality", "task_adherence", 0.79, False, True),
        ("safety", "attack_success_rate", 0.01, False, True),
        ("latency", "p95_ms", 8001, True, False),
        ("cost", "estimated_per_run", 0.0201, True, False),
        ("telemetry", "error_rate", 0.03, True, False),
    ],
)
def test_one_failing_rule_fails_only_its_own_health_and_the_whole(
    tmp_path: Path, card: str, metric: str, value: float, quality: bool, operational: bool
) -> None:
    metrics = {**GOOD, card: {metric: value}}

    result = report(tmp_path, metrics)

    assert result["passed"] is False
    assert (result["qualityHealth"], result["operationalHealth"]) == (quality, operational)
    assert len(result["failures"]) == 1 and card in result["failures"][0]


def test_rule_bounds_are_inclusive(tmp_path: Path) -> None:
    metrics = {
        **GOOD,
        "quality": {"task_adherence": 0.8},
        "latency": {"p95_ms": 8000},
        "policy": {"failures": 0},
    }

    assert report(tmp_path, metrics)["passed"] is True


@pytest.mark.parametrize("card", REQUIRED)
def test_a_missing_metric_fails_closed(tmp_path: Path, card: str) -> None:
    metrics = {k: v for k, v in GOOD.items() if k != card}

    result = report(tmp_path, metrics)

    assert result["passed"] is False
    assert any(card in failure and "missing" in failure for failure in result["failures"])


@pytest.mark.parametrize("card", REQUIRED)
def test_thresholds_must_cover_every_required_scorecard(tmp_path: Path, card: str) -> None:
    rules = {k: v for k, v in RULES.items() if k != card}

    with pytest.raises(ConfigurationError, match=f"no rule for the '{card}' scorecard"):
        load_thresholds(write(tmp_path, "t.yaml", thresholds_text(rules)))


BAD_RULES = [
    "{scorecard: nope, metric: x, maximum: 1}",
    "{scorecard: policy, metric: x}",
    "{scorecard: policy, metric: x, minimum: 1, maximum: 2}",
    "{scorecard: policy, metric: 'Bad Name', maximum: 1}",
    "{scorecard: policy, metric: 'a|b', maximum: 1}",
    "{scorecard: policy, metric: 'ok\\n| x', maximum: 1}",
    "{scorecard: policy, metric: Upper, maximum: 1}",
    "{scorecard: policy, metric: 1x, maximum: 1}",
    "{scorecard: policy, metric: x, maximum: .nan}",
    "{scorecard: policy, metric: x, maximum: .inf}",
    "{scorecard: policy, metric: x, maximum: true}",
    "{scorecard: policy, metric: x, minimum: false}",
    "{scorecard: policy, metric: x, maximum: 1, extra: 1}",
]


def test_the_baseline_thresholds_are_valid(tmp_path: Path) -> None:
    assert len(load_thresholds(write(tmp_path, "t.yaml", thresholds_text())).rules) == 6


@pytest.mark.parametrize("rule", BAD_RULES)
def test_one_bad_rule_rejects_an_otherwise_valid_file(tmp_path: Path, rule: str) -> None:
    text = thresholds_text() + f"  - {rule}\n"

    with pytest.raises(ConfigurationError):
        load_thresholds(write(tmp_path, "t.yaml", text))


@pytest.mark.parametrize(
    "text",
    [
        "version: 2\nrules: []\n",
        "version: 1\nrules: []\n",
        thresholds_text() + "unknown: 1\n",
        thresholds_text(extra="regressionTolerance: 1.5"),
        thresholds_text(extra="regressionTolerance: -0.1"),
        thresholds_text(extra="regressionTolerance: true"),
        "not: [valid",
        "",
    ],
)
def test_malformed_thresholds_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigurationError):
        load_thresholds(write(tmp_path, "t.yaml", text))


def test_the_same_metric_cannot_have_two_rules(tmp_path: Path) -> None:
    text = thresholds_text() + "  - {scorecard: latency, metric: p95_ms, maximum: 1}\n"

    with pytest.raises(ConfigurationError, match="more than one rule"):
        load_thresholds(write(tmp_path, "t.yaml", text))


def test_a_missing_thresholds_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_thresholds(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"policy": 1}',
        '{"nope": {"x": 1}}',
        '{"policy": {"Bad Name": 1}}',
        '{"policy": {"x": true}}',
        '{"policy": {"x": "1"}}',
        '{"policy": {"x": NaN}}',
        '{"policy": {"x": Infinity}}',
        '{"policy": {"x": 1e999}}',
        '{"policy": {"x": null}}',
    ],
)
def test_malformed_metric_files_are_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError):
        load_metrics(write(tmp_path, "m.json", content))


def test_a_valid_metric_file_loads_as_numbers(tmp_path: Path) -> None:
    path = write(tmp_path, "m.json", {"quality": {"task_adherence": 1, "groundedness": 0.5}})

    assert load_metrics(path) == {"quality": {"task_adherence": 1.0, "groundedness": 0.5}}


JUNIT = (
    '<testsuites><testsuite name="pytest" tests="10" failures="1" errors="2" skipped="3">'
    "</testsuite></testsuites>"
)


def test_junit_counts_failures_and_errors_together(tmp_path: Path) -> None:
    metrics = junit_metrics(write(tmp_path, "j.xml", JUNIT))

    assert metrics == {"policy": {"tests": 10.0, "failures": 3.0, "skipped": 3.0}}


def test_junit_sums_every_suite(tmp_path: Path) -> None:
    text = (
        '<testsuites><testsuite tests="3" failures="1" errors="0" skipped="0"/>'
        '<testsuite tests="4" failures="0" errors="1" skipped="2"/></testsuites>'
    )

    assert junit_metrics(write(tmp_path, "j.xml", text)) == {
        "policy": {"tests": 7.0, "failures": 2.0, "skipped": 2.0}
    }


def test_junit_accepts_a_single_testsuite_root(tmp_path: Path) -> None:
    text = '<testsuite tests="4" failures="0" errors="0" skipped="0"></testsuite>'

    assert junit_metrics(write(tmp_path, "j.xml", text))["policy"]["tests"] == 4.0


@pytest.mark.parametrize(
    "content",
    [
        "<testsuites>",
        "<testsuites></testsuites>",
        "<other/>",
        '<other><testsuite tests="1" failures="0" errors="0" skipped="0"/></other>',
        '<testsuite tests="x" failures="0" errors="0" skipped="0"/>',
        '<testsuite tests="-1" failures="0" errors="0" skipped="0"/>',
        '<testsuite tests="1" failures="0" errors="0"/>',
        '<!DOCTYPE x [<!ENTITY a "b">]><testsuite tests="1" failures="0" errors="0" skipped="0"/>',
        "<!doctype x><testsuite/>",
    ],
)
def test_unsafe_or_malformed_junit_is_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError):
        junit_metrics(write(tmp_path, "j.xml", content))


@pytest.mark.parametrize("kind", ["junit", "metrics", "thresholds", "retrieval"])
def test_oversized_inputs_are_refused_before_parsing(tmp_path: Path, kind: str) -> None:
    path = write(tmp_path, "big", "x" * (5 * 1024 * 1024 + 1))
    loaders = {
        "junit": junit_metrics,
        "metrics": load_metrics,
        "thresholds": load_thresholds,
        "retrieval": retrieval_metrics,
    }

    with pytest.raises(ConfigurationError, match="larger than"):
        loaders[kind](path)


def test_a_file_at_the_size_limit_is_still_read(tmp_path: Path) -> None:
    padding = " " * (5 * 1024 * 1024 - 2)
    path = write(tmp_path, "edge.json", "{}" + padding)

    assert load_metrics(path) == {}


def test_retrieval_metrics_are_read_from_the_all_category(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "r.json",
        {
            "metrics": {
                "all": {"answer_rate": 0.59, "false_answer_rate": 0.0, "source_recall": None},
                "exact": {"answer_rate": 1.0},
            }
        },
    )

    assert retrieval_metrics(path) == {"retrieval": {"answer_rate": 0.59, "false_answer_rate": 0.0}}


@pytest.mark.parametrize("content", ["not json", "[]", "{}", '{"metrics": {}}', '{"metrics": 1}'])
def test_a_malformed_retrieval_result_is_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError):
        retrieval_metrics(write(tmp_path, "r.json", content))


def test_cost_is_derived_from_customer_prices_and_average_tokens(tmp_path: Path) -> None:
    prices = "prices: {inputPerMillionTokens: 2.0, outputPerMillionTokens: 8.0}"
    metrics = {
        **GOOD,
        "telemetry": {"error_rate": 0, "avg_input_tokens": 3000, "avg_output_tokens": 500},
    }
    metrics["cost"] = {}

    result = report(tmp_path, metrics, thresholds_text(prices=prices))

    assert result["metrics"]["cost"]["estimated_per_run"] == pytest.approx(0.01)
    assert result["passed"] is True


def test_derived_cost_never_overrides_a_reported_value_and_needs_prices(tmp_path: Path) -> None:
    metrics = {
        **GOOD,
        "telemetry": {"error_rate": 0, "avg_input_tokens": 1e9, "avg_output_tokens": 1e9},
    }
    prices = "prices: {inputPerMillionTokens: 2.0, outputPerMillionTokens: 8.0}"

    reported = report(tmp_path, metrics, thresholds_text(prices=prices))
    unpriced = report(tmp_path, {**metrics, "cost": {}})

    assert reported["metrics"]["cost"]["estimated_per_run"] == 0.01
    assert "estimated_per_run" not in unpriced["metrics"]["cost"]
    assert unpriced["passed"] is False


def test_prices_must_be_finite_and_non_negative(tmp_path: Path) -> None:
    for bad in ("-1", ".nan", ".inf"):
        prices = f"prices: {{inputPerMillionTokens: {bad}, outputPerMillionTokens: 1}}"
        with pytest.raises(ConfigurationError):
            load_thresholds(write(tmp_path, "t.yaml", thresholds_text(prices=prices)))


@pytest.mark.parametrize(
    ("value", "passes"),
    [(0.9, True), (0.86, True), (0.855, True), (0.85, True), (0.8499, False), (0.8, False)],
)
def test_a_floor_metric_may_not_fall_more_than_the_tolerance_below_baseline(
    tmp_path: Path, value: float, passes: bool
) -> None:
    metrics = {**GOOD, "quality": {"task_adherence": value}}
    baseline = {"quality": {"task_adherence": 1.0}}
    text = thresholds_text(extra="regressionTolerance: 0.15")
    text = text.replace("minimum: 0.8", "minimum: 0.5")

    result = report(tmp_path, metrics, text, baseline)

    assert result["passed"] is passes
    assert (not passes) == any("regress" in f for f in result["failures"])


def test_a_ceiling_metric_may_not_rise_more_than_the_tolerance_above_baseline(
    tmp_path: Path,
) -> None:
    text = thresholds_text(extra="regressionTolerance: 0.1")
    baseline = {"latency": {"p95_ms": 4000}}

    fine = report(tmp_path, {**GOOD, "latency": {"p95_ms": 4400}}, text, baseline)
    slower = report(tmp_path, {**GOOD, "latency": {"p95_ms": 4401}}, text, baseline)

    assert fine["passed"] is True
    assert slower["passed"] is False and "regress" in slower["failures"][0]


def test_any_rise_above_a_zero_baseline_ceiling_is_a_regression(tmp_path: Path) -> None:
    text = thresholds_text().replace("failures, maximum: 0", "failures, maximum: 5")
    baseline = {"policy": {"failures": 0}}

    result = report(tmp_path, {**GOOD, "policy": {"failures": 1}}, text, baseline)

    assert result["passed"] is False and "regress" in result["failures"][0]


def test_a_failed_rule_stays_failed_when_a_baseline_is_given(tmp_path: Path) -> None:
    slow = {**GOOD, "latency": {"p95_ms": 9000}}

    result = report(tmp_path, slow, baseline={"latency": {"p95_ms": 9000}})

    assert result["passed"] is False and "above the maximum" in result["failures"][0]


def test_the_tolerance_is_relative_to_the_size_of_a_negative_baseline(tmp_path: Path) -> None:
    text = thresholds_text(extra="regressionTolerance: 0.1")
    baseline = {"policy": {"failures": -10}}

    within = report(tmp_path, {**GOOD, "policy": {"failures": -9}}, text, baseline)
    beyond = report(tmp_path, {**GOOD, "policy": {"failures": -8.9}}, text, baseline)

    assert within["passed"] is True
    assert beyond["passed"] is False and "regress" in beyond["failures"][0]


def test_improvements_and_metrics_without_a_baseline_never_regress(tmp_path: Path) -> None:
    baseline = {"quality": {"task_adherence": 0.82}, "telemetry": {"unrelated": 1}}

    result = report(tmp_path, GOOD, baseline=baseline)

    assert result["passed"] is True


def test_the_report_lists_labels_and_both_verdicts(tmp_path: Path) -> None:
    labels = {"model": "gpt-5-mini", "dataset": "grounded-relevance@1"}
    limits = load_thresholds(write(tmp_path, "t.yaml", thresholds_text()))
    built = build_report(limits, {**GOOD, "latency": {"p95_ms": 9000}}, labels=labels)

    markdown = render_markdown(built)

    assert "# Release report" in markdown and "**FAIL**" in markdown
    assert "Quality health: **PASS**" in markdown and "Operational health: **FAIL**" in markdown
    assert "model: gpt-5-mini" in markdown and "dataset: grounded-relevance@1" in markdown
    assert "| latency | p95_ms | 9000 | <= 8000 | FAIL |" in markdown
    assert "| quality | task_adherence | 0.9 | >= 0.8 | PASS |" in markdown


def test_the_report_shows_missing_metrics_and_lists_failures_only_when_there_are_any(
    tmp_path: Path,
) -> None:
    limits = load_thresholds(write(tmp_path, "t.yaml", thresholds_text()))
    gap = {k: v for k, v in GOOD.items() if k != "latency"}

    failing = render_markdown(build_report(limits, gap, labels={}))
    passing = render_markdown(build_report(limits, GOOD, labels={}))

    assert "| latency | p95_ms | missing | <= 8000 | FAIL |" in failing
    assert "## Failures" in failing and "- latency.p95_ms is missing" in failing
    assert "## Failures" not in passing and "**FAIL**" not in passing


@pytest.mark.parametrize("label", ["a b", "a|b", "<x>", "", "a\nb", "x" * 129, "`x`"])
def test_labels_that_could_alter_the_report_are_rejected(tmp_path: Path, label: str) -> None:
    limits = load_thresholds(write(tmp_path, "t.yaml", thresholds_text()))

    with pytest.raises(ConfigurationError):
        build_report(limits, GOOD, labels={"key": label})
    with pytest.raises(ConfigurationError):
        build_report(limits, GOOD, labels={label: "value"})


def test_at_most_twenty_labels_are_accepted(tmp_path: Path) -> None:
    limits = load_thresholds(write(tmp_path, "t.yaml", thresholds_text()))

    build_report(limits, GOOD, labels={f"k{i}": "v" for i in range(20)})
    with pytest.raises(ConfigurationError):
        build_report(limits, GOOD, labels={f"k{i}": "v" for i in range(21)})


def inputs(tmp_path: Path, **overrides: dict[str, float]) -> list[str]:
    metrics = {**GOOD, **overrides}
    junit = write(
        tmp_path, "j.xml", JUNIT.replace('failures="1" errors="2"', 'failures="0" errors="0"')
    )
    return [
        "release-report",
        "--thresholds",
        str(write(tmp_path, "t.yaml", thresholds_text())),
        "--junit",
        str(junit),
        "--metrics",
        str(write(tmp_path, "m.json", {k: v for k, v in metrics.items() if k != "policy"})),
    ]


def test_the_command_writes_both_reports_and_exits_zero_on_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, markdown = tmp_path / "out.json", tmp_path / "report.md"

    code = main(
        [
            *inputs(tmp_path),
            "--json-out",
            str(out),
            "--markdown",
            str(markdown),
            "--label",
            "model=gpt-5-mini",
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert "model: gpt-5-mini" in markdown.read_text(encoding="utf-8")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["policy"]["tests"] == 10 and saved["quality"] == {"task_adherence": 0.9}


def test_the_saved_metrics_work_as_a_baseline_for_the_next_run(tmp_path: Path) -> None:
    out = tmp_path / "baseline.json"
    assert main([*inputs(tmp_path), "--json-out", str(out)]) == 0

    slipping = inputs(tmp_path, quality={"task_adherence": 0.85})
    assert main(slipping) == 0
    assert main([*slipping, "--baseline", str(out)]) == 1


def test_the_command_exits_one_on_a_failed_rule_and_two_on_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(inputs(tmp_path, latency={"p95_ms": 99999})) == 1
    capsys.readouterr()

    bad = ["release-report", "--thresholds", str(tmp_path / "absent.yaml")]
    assert main(bad) == 2
    assert "configuration_error" in capsys.readouterr().err


@pytest.mark.parametrize("labels", [["noequals"], ["a=1", "a=2"], ["=value"]])
def test_run_labels_must_be_unique_key_value_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], labels: list[str]
) -> None:
    args = inputs(tmp_path)
    for label in labels:
        args += ["--label", label]

    assert main(args) == 2
    assert "configuration_error" in capsys.readouterr().err


def test_a_malformed_label_pair_is_named_in_the_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*inputs(tmp_path), "--label", "noequals"]) == 2
    assert "unique KEY=VALUE" in capsys.readouterr().err


def test_two_sources_cannot_report_the_same_metric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = inputs(tmp_path)
    args += ["--metrics", str(write(tmp_path, "m2.json", {"quality": {"task_adherence": 0.1}}))]

    assert main(args) == 2
    assert "reported twice" in capsys.readouterr().err
