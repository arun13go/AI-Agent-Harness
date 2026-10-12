# pyright: reportUnknownMemberType=false

"""Load typed profiles using the versioned harness schema."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, cast

import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_harness.core.errors import ConfigurationError
from agent_harness.core.types import JsonValue


class ProfileName(StrEnum):
    ASSISTANT = "assistant"
    GROUNDED_ASSISTANT = "grounded-assistant"
    ACTION_AGENT = "action-agent"


class MaximumTier(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    ACT = "act"


class HarnessModel(BaseModel):
    """Base immutable model that rejects unknown settings."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class RuntimeConfig(HarnessModel):
    adapter: Literal["maf"]
    max_steps: int = Field(alias="maxSteps", ge=1, le=64)
    max_tool_calls: int = Field(alias="maxToolCalls", ge=1, le=64)
    timeout_seconds: int = Field(alias="timeoutSeconds", ge=1, le=900)


class ModelConfig(HarnessModel):
    provider: Literal["foundry"]
    deployment_name_env: str = Field(alias="deploymentNameEnv", pattern=r"^[A-Z][A-Z0-9_]*$")


class ContextConfig(HarnessModel):
    compaction: Literal["truncate-oldest", "summarize"]
    max_input_tokens: int = Field(alias="maxInputTokens", ge=1024, le=1_048_576)


class AnswerabilityConfig(HarnessModel):
    enabled: bool
    on_insufficient_evidence: Literal["clarify", "caveat", "abstain", "escalate"] = Field(
        alias="onInsufficientEvidence"
    )


class RetrievalConfig(HarnessModel):
    enabled: bool
    adapter: Literal["none", "azure-ai-search"]
    strategy: Literal["none", "keyword", "vector", "hybrid"]
    answerability: AnswerabilityConfig


class MemoryConfig(HarnessModel):
    adapter: Literal["in-memory", "cosmos-db"]
    ttl_days: int = Field(alias="ttlDays", ge=1, le=3650)


class StateConfig(HarnessModel):
    adapter: Literal["in-memory", "cosmos-db"]


class GovernanceConfig(HarnessModel):
    maximum_tier: MaximumTier = Field(alias="maximumTier")
    approval_required_for_act: Literal[True] = Field(alias="approvalRequiredForAct")
    audit_enabled: Literal[True] = Field(alias="auditEnabled")


class ObservabilityConfig(HarnessModel):
    exporter: Literal["console", "application-insights"]
    payload_capture: Literal[False] = Field(alias="payloadCapture")


class HarnessConfig(HarnessModel):
    """Represent one fully validated harness profile."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    profile: ProfileName
    runtime: RuntimeConfig
    model: ModelConfig
    context: ContextConfig
    retrieval: RetrievalConfig
    memory: MemoryConfig
    state: StateConfig
    governance: GovernanceConfig
    observability: ObservabilityConfig


def _load_mapping(path: Path) -> dict[str, JsonValue]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except OSError as error:
        raise ConfigurationError(f"Cannot read configuration '{path}': {error}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Invalid YAML in '{path}': {error}") from error

    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration '{path}' must contain a YAML object")
    return cast(dict[str, JsonValue], value)


def _load_schema(path: Path) -> dict[str, JsonValue]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except OSError as error:
        raise ConfigurationError(f"Cannot read configuration schema '{path}': {error}") from error
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"Invalid JSON Schema in '{path}': {error}") from error

    if not isinstance(value, dict):
        raise ConfigurationError(f"Configuration schema '{path}' must contain a JSON object")
    return cast(dict[str, JsonValue], value)


def load_harness_config(profile_path: Path, schema_path: Path) -> HarnessConfig:
    """Load, schema-check, and type-check a profile."""

    raw_config = _load_mapping(profile_path)
    schema = _load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw_config), key=lambda error: list(error.absolute_path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ConfigurationError(
            f"Configuration '{profile_path}' failed schema validation: {details}"
        )

    try:
        return HarnessConfig.model_validate(raw_config)
    except ValidationError as error:
        raise ConfigurationError(
            f"Configuration '{profile_path}' failed typed validation: {error}"
        ) from error
