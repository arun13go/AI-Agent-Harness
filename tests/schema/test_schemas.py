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


@pytest.mark.parametrize(
    ("profile", "changes"),
    [
        ("grounded-assistant-local", {"strategy": "keyword"}),
        ("grounded-assistant-local", {"strategy": "hybrid"}),
        ("grounded-assistant-local", {"adapter": "none", "strategy": "none"}),
        ("grounded-assistant-local", {"enabled": False, "adapter": "none", "strategy": "none"}),
        ("grounded-assistant", {"strategy": "none"}),
        ("grounded-assistant", {"adapter": "none", "strategy": "none"}),
        ("grounded-assistant", {"adapter": "in-memory", "strategy": "hybrid"}),
    ],
)
def test_grounded_profile_adapter_and_strategy_must_be_a_consistent_pair(
    profile: str, changes: dict[str, object]
) -> None:
    document = deepcopy(load_yaml(PROFILE_DIR / f"{profile}.yaml"))
    cast(dict[str, object], document["retrieval"]).update(changes)

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(document)


def test_grounded_profile_cannot_disable_answerability_for_any_adapter() -> None:
    for profile in ("grounded-assistant", "grounded-assistant-local"):
        document = deepcopy(load_yaml(PROFILE_DIR / f"{profile}.yaml"))
        retrieval = cast(dict[str, object], document["retrieval"])
        cast(dict[str, object], retrieval["answerability"])["enabled"] = False

        with pytest.raises(ValidationError):
            validator("harness.schema.json").validate(document)


def azure_profile() -> dict[str, object]:
    return deepcopy(load_yaml(PROFILE_DIR / "grounded-assistant.yaml"))


def search_block(document: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], cast(dict[str, object], document["retrieval"])["search"])


@pytest.mark.parametrize("strategy", ["keyword", "semantic", "vector", "hybrid"])
def test_azure_strategies_the_schema_allows(strategy: str) -> None:
    document = azure_profile()
    cast(dict[str, object], document["retrieval"])["strategy"] = strategy

    validator("harness.schema.json").validate(document)


def test_keyword_strategy_accepts_a_score_half_point() -> None:
    document = azure_profile()
    cast(dict[str, object], document["retrieval"])["strategy"] = "keyword"
    search_block(document)["keywordScoreHalfPoint"] = 2.5

    validator("harness.schema.json").validate(document)


_REMOVE = object()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("retrieval", "search", _REMOVE),
        ("retrieval.search", "extra", "x"),
        ("retrieval.search", "indexName", "Upper"),
        ("retrieval.search", "indexName", "-leading"),
        ("retrieval.search", "indexName", "has space"),
        ("retrieval.search", "indexName", ""),
        ("retrieval.search", "indexName", "a" * 129),
        ("retrieval.search", "endpointEnv", "lower"),
        ("retrieval.search", "endpointEnv", "1BAD"),
        ("retrieval.search", "endpointEnv", "HAS-DASH"),
        ("retrieval.search", "keywordScoreHalfPoint", 2.0),
        ("retrieval", "limit", 0),
        ("retrieval", "limit", 101),
        ("retrieval", "minimumScore", -0.1),
        ("retrieval", "minimumScore", 1.1),
        ("retrieval", "maximumAttempts", -1),
        ("retrieval", "maximumAttempts", 5),
    ],
)
def test_invalid_retrieval_settings_are_rejected(section: str, key: str, value: object) -> None:
    document = azure_profile()
    target: dict[str, object] = document
    for part in section.split("."):
        target = cast(dict[str, object], target[part])
    if value is _REMOVE:
        del target[key]
    else:
        target[key] = value

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(document)


@pytest.mark.parametrize("half_point", [0, -1, 1_000_001])
def test_keyword_half_point_must_be_positive_and_bounded(half_point: float) -> None:
    document = azure_profile()
    cast(dict[str, object], document["retrieval"])["strategy"] = "keyword"
    search_block(document)["keywordScoreHalfPoint"] = half_point

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(document)


@pytest.mark.parametrize("adapter", ["in-memory", "none"])
def test_a_search_block_is_rejected_for_other_adapters(adapter: str) -> None:
    document = deepcopy(load_yaml(PROFILE_DIR / "grounded-assistant-local.yaml"))
    retrieval = cast(dict[str, object], document["retrieval"])
    retrieval["search"] = {"endpointEnv": "AZURE_SEARCH_ENDPOINT", "indexName": "chunks"}
    if adapter == "none":
        document = deepcopy(load_yaml(PROFILE_DIR / "assistant.yaml"))
        cast(dict[str, object], document["retrieval"])["search"] = {
            "endpointEnv": "AZURE_SEARCH_ENDPOINT",
            "indexName": "chunks",
        }

    with pytest.raises(ValidationError):
        validator("harness.schema.json").validate(document)


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
