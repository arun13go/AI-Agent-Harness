"""Resolve caller authorization labels from the deployment, never from a request."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from agent_harness.core.errors import ConfigurationError, InputValidationError
from agent_harness.core.governance import validate_authorization_labels
from agent_harness.core.types import IdentityScope

CALLER_LABELS_ENVIRONMENT_VARIABLE = "HARNESS_CALLER_LABELS"
_MAXIMUM_ENVIRONMENT_CHARACTERS = 16_384


@dataclass(frozen=True, slots=True)
class DeploymentCallerLabels:
    """Give every authenticated caller of this deployment the same fixed label set.

    The set is the deployment's own decision, made by whoever operates the endpoint, so a
    request, a profile, a tool or a model can never widen it. Per-user labels need a resolver
    backed by a directory and are a separate decision.
    """

    labels: tuple[str, ...]

    async def resolve(self, identity: IdentityScope) -> Sequence[str]:
        del identity
        return self.labels


def load_deployment_caller_labels(environ: Mapping[str, str]) -> DeploymentCallerLabels:
    """Parse ``HARNESS_CALLER_LABELS`` strictly, failing startup on any defect.

    The value must be a JSON array of distinct label strings. An empty array is valid and
    means callers see only unlabeled evidence. A missing value is an error so that forgetting
    the setting can never silently pick a scope.
    """

    raw = environ.get(CALLER_LABELS_ENVIRONMENT_VARIABLE)
    if raw is None:
        raise ConfigurationError(
            f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} is required; use [] for unlabeled evidence only"
        )
    if len(raw) > _MAXIMUM_ENVIRONMENT_CHARACTERS:
        raise ConfigurationError(f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} is too large")
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise ConfigurationError(
            f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} must be a JSON array of strings"
        ) from error
    if not isinstance(parsed, list):
        raise ConfigurationError(
            f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} must be a JSON array of strings"
        )
    candidates = cast("list[object]", parsed)
    if not all(isinstance(label, str) for label in candidates):
        raise ConfigurationError(
            f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} must be a JSON array of strings"
        )
    labels_in = cast("list[str]", candidates)
    if len(set(labels_in)) != len(labels_in):
        raise ConfigurationError(f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} contains duplicate labels")
    try:
        labels = validate_authorization_labels(labels_in)
    except InputValidationError as error:
        raise ConfigurationError(
            f"{CALLER_LABELS_ENVIRONMENT_VARIABLE} contains an invalid or excessive label set"
        ) from error
    return DeploymentCallerLabels(labels)
