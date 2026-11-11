"""Check the evaluation-row export: exact Foundry row shape, strict input files, stable order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_harness.app.cli import main
from agent_harness.app.eval_export import export_rows, load_assistant_rows
from agent_harness.core.errors import ConfigurationError

ROOT = Path(__file__).parents[2]
GROUNDED = ROOT / "evals" / "grounded-relevance"
ASSISTANT = ROOT / "evals" / "release" / "assistant.yaml"

VALID = """\
version: 1
name: demo
rows:
  - {id: a01, category: tool-call, query: "Is it up?", expected_behavior: "Calls the tool."}
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rows.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_grounded_rows_follow_the_question_set_and_carry_only_foundry_fields() -> None:
    rows = export_rows("grounded", grounded_dir=GROUNDED, assistant_file=ASSISTANT)

    assert len(rows) == 33
    assert all(set(row) == {"query", "expected_behavior"} for row in rows)
    assert rows[0]["query"] == "How many days do customers have to pay an invoice?"
    assert rows[-1]["query"].startswith("Who is the incident commander")


def test_answerable_rows_name_their_sources_and_unanswerable_rows_forbid_guessing() -> None:
    rows = export_rows("grounded", grounded_dir=GROUNDED, assistant_file=ASSISTANT)
    by_query = {row["query"]: row["expected_behavior"] for row in rows}

    single = by_query["How many days do customers have to pay an invoice?"]
    double = by_query["How long are logs kept and how long are backups kept?"]
    refusal = by_query["What is the capital of France?"]

    assert "billing.md" in single and "cites" in single
    assert "data-retention.md" in double and "backup-and-recovery.md" in double
    assert "does not guess" in refusal.lower() and "outside knowledge" in refusal.lower()
    assert "declines" in refusal and "answers" not in refusal.lower()
    assert "billing.md" not in refusal


def test_every_grounded_row_is_either_an_answer_or_a_refusal() -> None:
    rows = export_rows("grounded", grounded_dir=GROUNDED, assistant_file=ASSISTANT)
    behaviors = {row["expected_behavior"].split(" ")[0] for row in rows}

    assert behaviors == {"Answers", "Does"}


def test_the_shipped_assistant_rows_cover_tools_scope_and_injection() -> None:
    rows = load_assistant_rows(ASSISTANT)

    assert {row.category for row in rows} >= {"tool-call", "tool-unavailable", "scope", "injection"}
    assert len({row.id for row in rows}) == len(rows)
    exported = export_rows("assistant", grounded_dir=GROUNDED, assistant_file=ASSISTANT)
    assert [row["query"] for row in exported] == [row.query for row in rows]
    assert all(set(row) == {"query", "expected_behavior"} for row in exported)


def test_a_valid_assistant_file_loads(tmp_path: Path) -> None:
    rows = load_assistant_rows(write(tmp_path, VALID))

    assert [(row.id, row.category) for row in rows] == [("a01", "tool-call")]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not: [valid",
        VALID.replace("version: 1", "version: 2"),
        VALID.replace("rows:", "unknown: 1\nrows:"),
        VALID.replace("}\n", ", extra: 1}\n"),
        VALID.replace("id: a01", "id: 'A 01'"),
        VALID.replace("category: tool-call", "category: 'Tool Call'"),
        VALID.replace('query: "Is it up?"', 'query: ""'),
        VALID.replace('query: "Is it up?"', "query: " + '"' + "q" * 2049 + '"'),
        VALID.replace('expected_behavior: "Calls the tool."', 'expected_behavior: ""'),
        VALID.replace(
            'expected_behavior: "Calls the tool."', "expected_behavior: " + '"' + "x" * 1025 + '"'
        ),
        VALID + "  - {id: a01, category: scope, query: q, expected_behavior: b}\n",
        "version: 1\nname: demo\nrows: []\n",
    ],
)
def test_malformed_assistant_files_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigurationError):
        load_assistant_rows(write(tmp_path, text))


def test_a_missing_assistant_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Cannot read"):
        load_assistant_rows(tmp_path / "absent.yaml")


def test_an_unknown_target_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="Unknown export target"):
        export_rows("other", grounded_dir=GROUNDED, assistant_file=ASSISTANT)


def test_the_command_writes_the_row_array_and_reports_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "rows.json"

    code = main(["export-eval-rows", "--target", "grounded", "--out", str(out)])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"rows": 33, "target": "grounded"}
    written: list[dict[str, str]] = json.loads(out.read_text(encoding="utf-8"))
    assert len(written) == 33
    assert set(written[0]) == {"query", "expected_behavior"}


def test_the_command_exports_the_assistant_rows(tmp_path: Path) -> None:
    out = tmp_path / "assistant.json"

    assert main(["export-eval-rows", "--target", "assistant", "--out", str(out)]) == 0

    exported: list[dict[str, str]] = json.loads(out.read_text(encoding="utf-8"))
    assert len(exported) == len(load_assistant_rows(ASSISTANT))


def test_the_command_reports_bad_input_as_a_configuration_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["export-eval-rows", "--target", "assistant", "--out", str(tmp_path / "o.json")]

    assert main([*args, "--assistant-file", str(tmp_path / "absent.yaml")]) == 2
    assert "configuration_error" in capsys.readouterr().err
    bad = tmp_path / "unwritable" / "o.json"
    assert main(["export-eval-rows", "--target", "grounded", "--out", str(bad)]) == 2
