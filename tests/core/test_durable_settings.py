"""Refuse durable memory and state settings that no composition honors yet (ADR 0033)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_harness.core.configuration import load_harness_config
from agent_harness.core.errors import ConfigurationError

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"
PROFILES = sorted((ROOT / "config" / "profiles").glob("*.yaml"))


def with_adapter(tmp_path: Path, profile: Path, block: str, adapter: str) -> Path:
    text = profile.read_text(encoding="utf-8")
    changed, count = re.subn(rf"(?m)^({block}:\n  adapter: )in-memory", rf"\g<1>{adapter}", text)
    assert count == 1, f"{profile.name} has no in-memory {block} block"
    path = tmp_path / profile.name
    path.write_text(changed, encoding="utf-8")
    return path


@pytest.mark.parametrize("block", ["memory", "state"])
@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_a_durable_adapter_is_refused_until_a_composition_uses_it(
    tmp_path: Path, profile: Path, block: str
) -> None:
    path = with_adapter(tmp_path, profile, block, "cosmos-db")

    with pytest.raises(ConfigurationError, match=rf"{block}.*'cosmos-db' is not implemented"):
        load_harness_config(path, SCHEMA)


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_every_shipped_profile_still_loads_with_in_memory_state(profile: Path) -> None:
    config = load_harness_config(profile, SCHEMA)

    assert (config.memory.adapter, config.state.adapter) == ("in-memory", "in-memory")
