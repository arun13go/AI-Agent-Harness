"""Verify the CLI composes and runs the local read path."""

import json
from pathlib import Path

import pytest

from agent_harness.app.cli import main

ROOT = Path(__file__).parents[2]


def test_cli_runs_local_read_path(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "run",
            "--profile",
            str(ROOT / "config" / "profiles" / "assistant.yaml"),
            "--config-schema",
            str(ROOT / "config" / "schema" / "v1" / "harness.schema.json"),
            "--event-schema",
            str(ROOT / "config" / "schema" / "v1" / "run-event.schema.json"),
            "--tenant",
            "tenant-0001",
            "--subject",
            "subject-0001",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["auditEventCount"] == 4


@pytest.mark.parametrize(
    ("decision", "expected_effects", "expected_events"),
    [("approved", 1, 9), ("denied", 0, 6)],
)
def test_cli_runs_action_lifecycle(
    capsys: pytest.CaptureFixture[str],
    decision: str,
    expected_effects: int,
    expected_events: int,
) -> None:
    exit_code = main(
        [
            "action-demo",
            "--profile",
            str(ROOT / "config" / "profiles" / "action-agent.yaml"),
            "--config-schema",
            str(ROOT / "config" / "schema" / "v1" / "harness.schema.json"),
            "--event-schema",
            str(ROOT / "config" / "schema" / "v1" / "run-event.schema.json"),
            "--tenant",
            "tenant-0001",
            "--subject",
            "subject-0001",
            "--approver",
            "approver-0001",
            "--decision",
            decision,
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["decision"] == decision
    assert output["effectCount"] == expected_effects
    assert output["auditEventCount"] == expected_events
