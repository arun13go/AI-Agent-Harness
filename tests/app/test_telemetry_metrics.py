"""Check the Application Insights query file and the loader for its JSON result."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agent_harness.app.cli import main
from agent_harness.app.release_report import SCORECARDS, telemetry_metrics
from agent_harness.core.errors import ConfigurationError
from tests.app.test_release_report import GOOD, thresholds_text, write

QUERY = Path(__file__).parents[2] / "evals" / "release" / "telemetry.kql"
COLUMNS = [
    "telemetry__runs",
    "telemetry__error_rate",
    "latency__p95_ms",
    "telemetry__avg_input_tokens",
    "telemetry__avg_output_tokens",
]
ROW = [40, 0.025, 4200.5, 3100.0, 410.0]


def result(
    columns: list[str] | None = None, rows: list[list[Any]] | None = None, tables: int = 1
) -> dict[str, Any]:
    names = columns or COLUMNS
    table = {
        "name": "PrimaryResult",
        "columns": [{"name": name, "type": "real"} for name in names],
        "rows": [ROW] if rows is None else rows,
    }
    return {"tables": [table] * tables}


def test_the_query_projects_only_known_scorecard_metric_columns() -> None:
    text = QUERY.read_text(encoding="utf-8")
    projected = re.findall(r"\b([a-z]+__[a-z0-9_]+)\s*=", text)

    assert sorted(projected) == sorted(COLUMNS)
    assert all(name.split("__")[0] in SCORECARDS for name in projected)


def test_the_query_reads_only_counts_and_timings_never_payload_fields() -> None:
    text = QUERY.read_text(encoding="utf-8")
    dimensions = set(re.findall(r'customDimensions\["([^"]+)"\]', text))

    assert dimensions == {"gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}
    assert not re.search(r"(?i)message|prompt|content|\bbody\b|properties", text.split("\n", 6)[-1])
    assert 'name == "harness.http"' in text and 'name == "harness.chat"' in text


def test_a_valid_result_becomes_normalized_metrics(tmp_path: Path) -> None:
    metrics = telemetry_metrics(write(tmp_path, "t.json", result()))

    assert metrics == {
        "telemetry": {
            "runs": 40.0,
            "error_rate": 0.025,
            "avg_input_tokens": 3100.0,
            "avg_output_tokens": 410.0,
        },
        "latency": {"p95_ms": 4200.5},
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        "{}",
        '{"tables": []}',
        '{"tables": "x"}',
        json.dumps(result(tables=2)),
        json.dumps(result(rows=[])),
        json.dumps(result(rows=[ROW, ROW])),
        json.dumps(result(rows=[ROW[:-1]])),
        json.dumps(result(rows=[[None, *ROW[1:]]])),
        json.dumps(result(rows=[["1", *ROW[1:]]])),
        json.dumps(result(rows=[[True, *ROW[1:]]])),
        json.dumps(result(columns=["nodoubleunderscore", *COLUMNS[1:]])),
        json.dumps(result(columns=["unknown__x", *COLUMNS[1:]])),
        json.dumps(result(columns=["telemetry__Bad Name", *COLUMNS[1:]])),
        json.dumps(result(columns=["telemetry__runs", "telemetry__runs", *COLUMNS[2:]])),
        json.dumps({"tables": [{"columns": "x", "rows": [ROW]}]}),
        json.dumps({"tables": [{"columns": [{"name": 1}] * 5, "rows": [ROW]}]}),
        json.dumps({"tables": [{"columns": [1] * 5, "rows": [ROW]}]}),
        json.dumps({"tables": [{"columns": [{"name": n} for n in COLUMNS], "rows": "x"}]}),
        json.dumps({"tables": [{"columns": [{"name": n} for n in COLUMNS], "rows": ["x"]}]}),
    ],
)
def test_malformed_or_empty_results_are_rejected(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError):
        telemetry_metrics(write(tmp_path, "t.json", content))


def test_a_null_value_points_at_missing_traffic(tmp_path: Path) -> None:
    path = write(tmp_path, "t.json", result(rows=[[0, None, None, None, None]]))

    with pytest.raises(ConfigurationError, match="no traffic"):
        telemetry_metrics(path)


def test_an_oversized_result_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path, "t.json", " " * (5 * 1024 * 1024 + 1))

    with pytest.raises(ConfigurationError, match="larger than"):
        telemetry_metrics(path)


def release_args(tmp_path: Path, telemetry: Path) -> list[str]:
    own = {card: values for card, values in GOOD.items() if card not in ("telemetry", "latency")}
    prices = "prices: {inputPerMillionTokens: 2.0, outputPerMillionTokens: 8.0}"
    own["cost"] = {}
    return [
        "release-report",
        "--thresholds",
        str(write(tmp_path, "thr.yaml", thresholds_text(prices=prices))),
        "--metrics",
        str(write(tmp_path, "m.json", own)),
        "--app-insights",
        str(telemetry),
    ]


def test_the_report_derives_cost_from_the_queried_token_averages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telemetry = write(tmp_path, "t.json", result(rows=[[40, 0.0, 4200.0, 3000.0, 500.0]]))

    assert main(release_args(tmp_path, telemetry)) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["metrics"]["cost"]["estimated_per_run"] == pytest.approx(0.01)
    assert report["metrics"]["latency"] == {"p95_ms": 4200.0}


def test_a_slow_or_failing_service_fails_operational_health_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telemetry = write(tmp_path, "t.json", result(rows=[[40, 0.5, 9000.0, 3000.0, 500.0]]))

    assert main(release_args(tmp_path, telemetry)) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["qualityHealth"] is True and report["operationalHealth"] is False


def test_telemetry_cannot_duplicate_a_metric_from_another_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    telemetry = write(tmp_path, "t.json", result())
    args = release_args(tmp_path, telemetry)
    args += ["--metrics", str(write(tmp_path, "m2.json", {"latency": {"p95_ms": 1}}))]

    assert main(args) == 2
    assert "reported twice" in capsys.readouterr().err
