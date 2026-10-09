"""Foundry model clients authenticated with Entra ID (no API keys).

Uses the Azure OpenAI v1 API through the OpenAI SDK, which works for OpenAI and
non-OpenAI Foundry deployments alike (chat completions).
"""

from __future__ import annotations

import os

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
ENDPOINT_ENV = "FOUNDRY_ENDPOINT"


def foundry_endpoint() -> str:
    """Return the Foundry resource endpoint, e.g. https://<name>.services.ai.azure.com."""
    endpoint = os.environ.get(ENDPOINT_ENV)
    if not endpoint:
        raise RuntimeError(f"{ENDPOINT_ENV} is not set")
    return endpoint.rstrip("/")


def make_openai_client(
    endpoint: str | None = None,
    *,
    credential: AsyncTokenCredential | None = None,
    max_retries: int = 6,
    timeout: float = 120.0,
) -> AsyncOpenAI:
    """Build an AsyncOpenAI client against the Foundry v1 endpoint with managed identity.

    Pass a credential you own (and close) to avoid leaking its HTTP session.
    """
    token_provider = get_bearer_token_provider(
        credential or DefaultAzureCredential(), FOUNDRY_SCOPE
    )
    return AsyncOpenAI(
        base_url=f"{endpoint or foundry_endpoint()}/openai/v1/",
        api_key=token_provider,
        max_retries=max_retries,
        timeout=timeout,
    )
