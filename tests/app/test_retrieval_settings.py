"""Prove retrieval settings flow from the profile into the grounded path and fail safely."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_harness.adapters.retrieval.azure_search import SCORE_KEY, evidence_to_document
from agent_harness.adapters.retrieval.azure_search_factory import create_azure_search_provider
from agent_harness.adapters.retrieval.in_memory import InMemoryRetrievalProvider
from agent_harness.adapters.storage.in_memory_audit import InMemoryAuditSink
from agent_harness.app.grounded_executor import (
    create_grounded_executor,
    ensure_grounded_profile_supported,
)
from agent_harness.core.configuration import HarnessConfig, RetrievalConfig, load_harness_config
from agent_harness.core.errors import ConfigurationError, InputValidationError
from agent_harness.core.types import (
    IdentityScope,
    Message,
    ModelRequest,
    ModelResponse,
    RequestContext,
    RetrievedItem,
    TokenUsage,
)
from tests.conformance.fixtures import IDENTITY_A, LABELS, NOW, make_evidence
from tests.conformance.test_azure_search_rest import Credential, FakeOpener, body_of

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "config" / "schema" / "v1" / "harness.schema.json"
EVENT_SCHEMA = ROOT / "config" / "schema" / "v1" / "run-event.schema.json"
AZURE_PROFILE = ROOT / "config" / "profiles" / "grounded-assistant.yaml"
ENV = {"AZURE_SEARCH_ENDPOINT": "https://srch-test.search.windows.net"}


def azure_config(
    tmp_path: Path,
    *,
    retrieval: Mapping[str, Any] | None = None,
    search: Mapping[str, Any] | None = None,
) -> HarnessConfig:
    document = yaml.safe_load(AZURE_PROFILE.read_text(encoding="utf-8"))
    document["retrieval"].update(retrieval or {})
    document["retrieval"]["search"].update(search or {})
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return load_harness_config(path, SCHEMA)


class Recording:
    """Record how the executor calls retrieval and return fixed evidence."""

    def __init__(self, items: Sequence[RetrievedItem]) -> None:
        self.calls: list[int] = []
        self._inner = InMemoryRetrievalProvider(items, clock=lambda: NOW)

    async def retrieve(
        self,
        *,
        identity: IdentityScope,
        authorization_labels: Sequence[str],
        query: str,
        limit: int,
    ) -> Sequence[RetrievedItem]:
        self.calls.append(limit)
        return await self._inner.retrieve(
            identity=identity, authorization_labels=authorization_labels, query=query, limit=limit
        )


class StubRuntime:
    """Return a fixed model answer so the answer route can complete without a model."""

    async def run(
        self,
        *,
        authenticated_identity: IdentityScope,
        request: ModelRequest,
        run_already_started: bool = False,
    ) -> ModelResponse:
        del authenticated_identity, request, run_already_started
        return ModelResponse(
            message=Message("assistant", "stub answer", "model-output"),
            tool_calls=(),
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            model_id="stub",
            finish_reason="stop",
        )


def compose(config: HarnessConfig, provider: Recording, **overrides: Any) -> Any:
    return create_grounded_executor(
        config=config,
        retrieval=provider,
        runtime=StubRuntime(),
        audit_sink=InMemoryAuditSink(EVENT_SCHEMA),
        clock=lambda: NOW,
        **overrides,
    )


async def ask_executor(executor: Any) -> Any:
    return await executor.run(
        authenticated_identity=IDENTITY_A,
        context=RequestContext(
            identity=IDENTITY_A,
            run_id="run-settings-0001",
            correlation_id="correlation-settings-0001",
            trace_id="0123456789abcdef0123456789abcdef",
            deadline=NOW + timedelta(hours=1),
        ),
        question="How do I restart the service?",
        authorization_labels=LABELS,
    )


def test_shipped_azure_profile_loads_with_its_documented_settings() -> None:
    config = load_harness_config(AZURE_PROFILE, SCHEMA)

    assert config.retrieval.strategy == "semantic"
    assert config.retrieval.minimum_score == 0.625
    assert config.retrieval.limit == 8
    assert config.retrieval.maximum_attempts == 1
    assert config.retrieval.search is not None
    assert config.retrieval.search.endpoint_env == "AZURE_SEARCH_ENDPOINT"
    ensure_grounded_profile_supported(config)


def test_local_profile_keeps_the_previous_defaults() -> None:
    config = load_harness_config(
        ROOT / "config" / "profiles" / "grounded-assistant-local.yaml", SCHEMA
    )

    assert (config.retrieval.minimum_score, config.retrieval.limit) == (0.5, 8)
    assert config.retrieval.maximum_attempts == 1
    assert config.retrieval.search is None


@pytest.mark.parametrize("strategy", ["vector", "hybrid"])
def test_strategies_without_an_implementation_are_refused_not_downgraded(
    tmp_path: Path, strategy: str
) -> None:
    config = azure_config(tmp_path, retrieval={"strategy": strategy})

    with pytest.raises(ConfigurationError, match=f"strategy '{strategy}' is not implemented"):
        ensure_grounded_profile_supported(config)
    with pytest.raises(ConfigurationError, match="is not implemented"):
        create_azure_search_provider(config, environ=ENV, credential=Credential())


@pytest.mark.parametrize("strategy", ["keyword", "semantic"])
def test_keyword_and_semantic_azure_profiles_are_supported(tmp_path: Path, strategy: str) -> None:
    config = azure_config(tmp_path, retrieval={"strategy": strategy})

    ensure_grounded_profile_supported(config)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("settings", "overrides", "expected_calls"),
    [
        ({"limit": 3, "maximumAttempts": 0}, {}, [3]),
        ({"limit": 3, "maximumAttempts": 1}, {}, [3, 6]),
        ({"limit": 3, "maximumAttempts": 2}, {}, [3, 6, 9]),
        ({"limit": 3, "maximumAttempts": 1}, {"retrieval_limit": 5}, [5, 10]),
        ({"limit": 3, "maximumAttempts": 1}, {"maximum_retrieval_attempts": 0}, [3]),
    ],
)
async def test_profile_limit_and_attempts_drive_retrieval_unless_overridden(
    tmp_path: Path,
    settings: dict[str, Any],
    overrides: dict[str, Any],
    expected_calls: list[int],
) -> None:
    provider = Recording([])
    executor = compose(azure_config(tmp_path, retrieval=settings), provider, **overrides)

    await ask_executor(executor)

    assert provider.calls == expected_calls


@pytest.mark.asyncio
async def test_profile_threshold_decides_whether_evidence_is_strong_enough(
    tmp_path: Path,
) -> None:
    evidence = [make_evidence("chunk-a", score=0.6)]
    strict = compose(azure_config(tmp_path, retrieval={"minimumScore": 0.9}), Recording(evidence))
    lenient = compose(azure_config(tmp_path, retrieval={"minimumScore": 0.5}), Recording(evidence))
    overridden = compose(
        azure_config(tmp_path, retrieval={"minimumScore": 0.9}),
        Recording(evidence),
        minimum_evidence_score=0.5,
    )

    assert (await ask_executor(strict)).route.value == "clarify"
    assert (await ask_executor(lenient)).route.value == "answer"
    assert (await ask_executor(overridden)).route.value == "answer"


@pytest.mark.parametrize("attempts", [-1, 5])
def test_attempts_outside_the_ceiling_are_rejected_at_composition(
    tmp_path: Path, attempts: int
) -> None:
    config = azure_config(tmp_path)

    with pytest.raises(ConfigurationError, match="Maximum retrieval attempts"):
        compose(config, Recording([]), maximum_retrieval_attempts=attempts)


def test_hand_built_retrieval_settings_are_validated_like_the_schema() -> None:
    base: dict[str, Any] = {
        "enabled": True,
        "adapter": "azure-ai-search",
        "strategy": "keyword",
        "answerability": {"enabled": True, "onInsufficientEvidence": "abstain"},
    }
    search = {"endpointEnv": "AZURE_SEARCH_ENDPOINT", "indexName": "chunks"}

    RetrievalConfig.model_validate({**base, "search": search})
    for bad in (
        {**base},
        {**base, "adapter": "in-memory", "strategy": "none", "search": search},
        {**base, "strategy": "semantic", "search": {**search, "keywordScoreHalfPoint": 2.0}},
        {**base, "adapter": "in-memory", "strategy": "semantic"},
        {**base, "search": search, "limit": 0},
        {**base, "search": search, "minimumScore": 1.5},
        {**base, "search": search, "maximumAttempts": 5},
    ):
        with pytest.raises(ValueError):
            RetrievalConfig.model_validate(bad)


# --- Provider factory ----------------------------------------------------------------------------


def provider_with(
    config: HarnessConfig, hits: list[dict[str, Any]], **kwargs: Any
) -> tuple[Any, FakeOpener]:
    opener = FakeOpener(lambda _: json.dumps({"value": hits}).encode())
    provider = create_azure_search_provider(
        config,
        environ=ENV,
        credential=Credential(),
        opener=opener,  # pyright: ignore[reportArgumentType]
        clock=lambda: NOW,
        **kwargs,
    )
    return provider, opener


async def ask(provider: Any) -> list[Any]:
    return list(
        await provider.retrieve(
            identity=IDENTITY_A, authorization_labels=(), query="restart", limit=5
        )
    )


def unlabeled_hit(**changes: Any) -> dict[str, Any]:
    item = make_evidence("chunk-a", content="Restart the service.")
    document = evidence_to_document(replace(item, authorization_labels=()))
    return {**document, **changes}


@pytest.mark.asyncio
async def test_semantic_profile_sends_semantic_queries_and_maps_the_reranker_scale(
    tmp_path: Path,
) -> None:
    config = azure_config(tmp_path)
    provider, opener = provider_with(
        config, [unlabeled_hit(**{SCORE_KEY: 9.0, "@search.rerankerScore": 2.0})]
    )

    [found] = await ask(provider)

    request = opener.requests[0]
    assert body_of(request)["queryType"] == "semantic"
    assert request.full_url.startswith(
        "https://srch-test.search.windows.net/indexes/harness-chunks/docs/search"
    )
    assert found.score == 0.5


@pytest.mark.asyncio
async def test_keyword_profile_uses_the_configured_half_point(tmp_path: Path) -> None:
    config = azure_config(
        tmp_path, retrieval={"strategy": "keyword"}, search={"keywordScoreHalfPoint": 3.0}
    )
    provider, opener = provider_with(config, [unlabeled_hit(**{SCORE_KEY: 3.0})])

    [found] = await ask(provider)

    assert body_of(opener.requests[0])["queryType"] == "simple"
    assert found.score == 0.5


@pytest.mark.asyncio
async def test_keyword_profile_defaults_the_half_point_to_one(tmp_path: Path) -> None:
    config = azure_config(tmp_path, retrieval={"strategy": "keyword"})
    provider, _ = provider_with(config, [unlabeled_hit(**{SCORE_KEY: 1.0})])

    [found] = await ask(provider)

    assert found.score == 0.5


@pytest.mark.asyncio
async def test_index_override_replaces_the_profile_index(tmp_path: Path) -> None:
    provider, opener = provider_with(azure_config(tmp_path), [], index_name="harness-eval")

    await ask(provider)

    assert "/indexes/harness-eval/docs/search" in opener.requests[0].full_url


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_endpoint_variable_is_a_configuration_error(
    tmp_path: Path, value: str | None
) -> None:
    environ: dict[str, str] = {} if value is None else {"AZURE_SEARCH_ENDPOINT": value}

    with pytest.raises(ConfigurationError, match="'AZURE_SEARCH_ENDPOINT' is not set"):
        create_azure_search_provider(
            azure_config(tmp_path), environ=environ, credential=Credential()
        )


def test_a_hostile_endpoint_value_is_rejected_before_any_token_is_requested(
    tmp_path: Path,
) -> None:
    credential = Credential()

    with pytest.raises(InputValidationError, match="Azure AI Search service URL") as raised:
        create_azure_search_provider(
            azure_config(tmp_path),
            environ={"AZURE_SEARCH_ENDPOINT": "https://evil.example"},
            credential=credential,
        )

    assert "evil.example" not in str(raised.value)
    assert credential.scopes == []


def test_the_factory_refuses_profiles_that_do_not_select_azure_search() -> None:
    config = load_harness_config(
        ROOT / "config" / "profiles" / "grounded-assistant-local.yaml", SCHEMA
    )

    with pytest.raises(ConfigurationError, match="does not select the azure-ai-search adapter"):
        create_azure_search_provider(config, environ=ENV, credential=Credential())
