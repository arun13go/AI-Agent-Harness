# pyright: reportUnknownMemberType=false

"""Verify profiles and run events satisfy their versioned schemas."""

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

ROOT = Path(__file__).parents[2]
SCHEMA_DIR = ROOT / "config" / "schema" / "v1"
PROFILE_DIR = ROOT / "config" / "profiles"


def load_json(path: Path) -> dict[str, object]:
    """Load a JSON object fixture."""

    with path.open(encoding="utf-8") as stream:
        return cast(dict[str, object], json.load(stream))


def load_yaml(path: Path) -> dict[str, object]:
    """Load a YAML object fixture."""

    with path.open(encoding="utf-8") as stream:
        return cast(dict[str, object], yaml.safe_load(stream))


def validator(name: str) -> Draft202012Validator:
    """Build a format-aware validator for a versioned schema."""

    schema = load_json(SCHEMA_DIR / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    "profile_path",
    sorted(PROFILE_DIR.glob("*.yaml")),
    ids=lambda path: path.stem,
)
def test_example_profile_is_valid(profile_path: Path) -> None:
    validator("harness.schema.json").validate(load_yaml(profile_path))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("governance", "approvalRequiredForAct"), False),
        (("governance", "auditEnabled"), False),
        (("observability", "payloadCapture"), True),
        (("runtime", "maxSteps"), 65),
    ],
)
def test_mandatory_controls_cannot_be_bypassed(
    path: tuple[str, str],
    value: object,
) -> None:
    profile = deepcopy(load_yaml(PROFILE_DIR / "action-agent.yaml"))
    section = cast(dict[str, object], profile[path[0]])
    section[path[1]] = value

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(profile)


def test_grounded_profile_requires_retrieval_and_answerability() -> None:
    profile = deepcopy(load_yaml(PROFILE_DIR / "grounded-assistant.yaml"))
    retrieval = cast(dict[str, object], profile["retrieval"])
    retrieval["enabled"] = False

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(profile)


def test_normalized_run_event_is_valid() -> None:
    event = {
        "schemaVersion": 1,
        "eventId": "event-0001",
        "eventType": "tool.proposed",
        "occurredAt": "2026-10-08T15:20:00Z",
        "sequence": 8,
        "tenantScope": "tenant-hash-0001",
        "subjectScope": "subject-hash-0001",
        "runId": "run-00000001",
        "correlationId": "correlation-0001",
        "traceId": "0123456789abcdef0123456789abcdef",
        "spanId": "0123456789abcdef",
        "source": "tool",
        "outcome": "pending-approval",
        "attributes": {
            "toolName": "update-record",
            "riskTier": "act",
            "proposalId": "proposal-0001",
        },
    }

    validator("run-event.schema.json").validate(event)


@pytest.mark.parametrize("forbidden_name", ["prompt", "messageContent", "tool_result", "secret"])
def test_run_event_rejects_payload_attributes(forbidden_name: str) -> None:
    event = {
        "schemaVersion": 1,
        "eventId": "event-0001",
        "eventType": "run.started",
        "occurredAt": "2026-10-08T15:20:00Z",
        "sequence": 0,
        "tenantScope": "tenant-hash-0001",
        "subjectScope": "subject-hash-0001",
        "runId": "run-00000001",
        "correlationId": "correlation-0001",
        "traceId": "0123456789abcdef0123456789abcdef",
        "source": "app",
        "outcome": "started",
        "attributes": {forbidden_name: "must-not-be-recorded"},
    }

    with pytest.raises(ValidationError):
        validator("run-event.schema.json").validate(event)
