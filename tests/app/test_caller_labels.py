"""Tests for strict deployment-level caller label configuration."""

from __future__ import annotations

import json

import pytest

from agent_harness.app.caller_labels import (
    CALLER_LABELS_ENVIRONMENT_VARIABLE,
    DeploymentCallerLabels,
    load_deployment_caller_labels,
)
from agent_harness.core.errors import ConfigurationError
from tests.conformance.fixtures import IDENTITY_A

NAME = CALLER_LABELS_ENVIRONMENT_VARIABLE


def load(value: str) -> DeploymentCallerLabels:
    return load_deployment_caller_labels({NAME: value})


def test_a_valid_array_is_canonicalized() -> None:
    assert load('["group:b", "group:a"]').labels == ("group:a", "group:b")


def test_an_empty_array_means_unlabeled_evidence_only() -> None:
    assert load("[]").labels == ()


def test_the_maximum_label_count_is_accepted() -> None:
    assert len(load(json.dumps([f"g{i}" for i in range(64)])).labels) == 64


def test_a_missing_value_is_a_startup_error_not_an_empty_scope() -> None:
    with pytest.raises(ConfigurationError, match="HARNESS_CALLER_LABELS is required"):
        load_deployment_caller_labels({})


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not json",
        "{}",
        '"group:a"',
        "null",
        '["group:a", 1]',
        '[["group:a"]]',
        '["group:a", "group:a"]',
        '["bad label"]',
        '[""]',
        json.dumps([f"g{i}" for i in range(65)]),
        '["' + "a" * 129 + '"]',
    ],
)
def test_every_malformed_value_fails_startup_without_echoing_it(value: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        load(value)

    assert "bad label" not in str(caught.value)


def test_an_oversized_value_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="too large"):
        load("[" + '"x",' * 20_000 + '"x"]')


@pytest.mark.asyncio
async def test_every_caller_gets_the_deployments_labels_regardless_of_identity() -> None:
    resolver = load('["group:ops"]')

    assert await resolver.resolve(IDENTITY_A) == ("group:ops",)
