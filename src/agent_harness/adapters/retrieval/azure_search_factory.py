"""Build the Azure AI Search provider a profile selects, keeping credentials out of the profile."""

from __future__ import annotations

import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime

from agent_harness.adapters.retrieval.azure_search import (
    AzureSearchRetrievalProvider,
    linear_score,
    saturating_score,
)
from agent_harness.adapters.retrieval.azure_search_rest import (
    AzureSearchRestBackend,
    TokenCredential,
)
from agent_harness.core.configuration import HarnessConfig
from agent_harness.core.errors import ConfigurationError

# The semantic ranker's reranker score runs from 0 to 4.
_RERANKER_MAXIMUM = 4.0
_DEFAULT_KEYWORD_HALF_POINT = 1.0


def create_azure_search_provider(
    config: HarnessConfig,
    *,
    environ: Mapping[str, str],
    index_name: str | None = None,
    credential: TokenCredential | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    clock: Callable[[], datetime] | None = None,
) -> AzureSearchRetrievalProvider:
    """Create the provider for ``retrieval.adapter: azure-ai-search`` from a validated profile.

    The service address is read from the environment variable the profile names. The strategy
    decides how service scores become relevance scores: the semantic reranker's 0 to 4 scale maps
    linearly, and raw keyword scores use the configured saturating half point.
    """

    settings = config.retrieval
    search = settings.search
    if settings.adapter != "azure-ai-search" or search is None:
        raise ConfigurationError("The profile does not select the azure-ai-search adapter")
    if settings.strategy not in ("keyword", "semantic"):
        raise ConfigurationError(
            f"Retrieval strategy '{settings.strategy}' is not implemented yet; "
            "use keyword or semantic"
        )
    endpoint = environ.get(search.endpoint_env, "").strip()
    if not endpoint:
        raise ConfigurationError(f"Environment variable '{search.endpoint_env}' is not set")
    semantic = settings.strategy == "semantic"
    if credential is None:
        from azure.identity import DefaultAzureCredential

        chosen: TokenCredential = DefaultAzureCredential()
    else:
        chosen = credential
    backend = AzureSearchRestBackend(
        endpoint=endpoint,
        index_name=index_name or search.index_name,
        credential=chosen,
        opener=opener,
        semantic=semantic,
    )
    half_point = search.keyword_score_half_point or _DEFAULT_KEYWORD_HALF_POINT
    return AzureSearchRetrievalProvider(
        backend,
        score_mapper=linear_score(_RERANKER_MAXIMUM) if semantic else saturating_score(half_point),
        clock=clock,
    )
