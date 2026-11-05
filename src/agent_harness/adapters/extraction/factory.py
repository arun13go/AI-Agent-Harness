"""Build the Document Intelligence extractor from the environment, keeping addresses out of code."""

from __future__ import annotations

import urllib.request
from collections.abc import Mapping

from agent_harness.adapters.extraction.document_intelligence import DocumentIntelligenceExtractor
from agent_harness.adapters.http_support import TokenCredential
from agent_harness.core.errors import ConfigurationError

ENDPOINT_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"


def create_document_extractor(
    environ: Mapping[str, str],
    *,
    credential: TokenCredential | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    max_file_bytes: int = 4 * 1024 * 1024,
) -> DocumentIntelligenceExtractor:
    """Create the extractor for the resource named by ``AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT``.

    The default file limit matches the free tier's 4 MiB; raise it for a paid resource. A missing
    or blank variable is a configuration error that names the variable, never a value.
    """

    endpoint = environ.get(ENDPOINT_ENV, "").strip()
    if not endpoint:
        raise ConfigurationError(f"Environment variable '{ENDPOINT_ENV}' is not set")
    if credential is None:
        from azure.identity import DefaultAzureCredential

        chosen: TokenCredential = DefaultAzureCredential()
    else:
        chosen = credential
    return DocumentIntelligenceExtractor(
        endpoint=endpoint, credential=chosen, opener=opener, max_file_bytes=max_file_bytes
    )
