"""Verify the CLI composes and runs the local read path."""

from pathlib import Path

from agent_harness.app.cli import main

ROOT = Path(__file__).parents[2]


def test_cli_runs_local_read_path(capsys: object) -> None:
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
