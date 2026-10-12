"""Verify profile loading and strict configuration rejection."""

from pathlib import Path

import pytest

from agent_harness.core.configuration import (
    ConfigurationError,
    MaximumTier,
    ProfileName,
    load_harness_config,
)

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"


def test_loads_valid_profile_into_strict_types() -> None:
    config = load_harness_config(ROOT / "config" / "profiles" / "assistant.yaml", SCHEMA)

    assert config.profile is ProfileName.ASSISTANT
    assert config.runtime.max_steps == 12
    assert config.runtime.max_tool_calls == 4
    assert config.governance.maximum_tier is MaximumTier.PROPOSE
    assert config.observability.payload_capture is False


def test_rejects_unknown_configuration_property(tmp_path: Path) -> None:
    profile = tmp_path / "invalid.yaml"
    profile.write_text(
        (ROOT / "config" / "profiles" / "assistant.yaml").read_text(encoding="utf-8")
        + "\nunknownSetting: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unknownSetting"):
        load_harness_config(profile, SCHEMA)


def test_rejects_missing_profile_file() -> None:
    with pytest.raises(ConfigurationError, match="Cannot read configuration"):
        load_harness_config(ROOT / "missing-profile.yaml", SCHEMA)
