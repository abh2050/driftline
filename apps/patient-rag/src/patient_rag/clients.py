"""Model and search clients for patient-rag, all authenticated with Entra ID."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from azure.identity.aio import DefaultAzureCredential
from openai import AsyncOpenAI

from driftline_common.foundry import FOUNDRY_SCOPE, make_openai_client

COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass
class ChatResult:
    text: str
    tokens_in: int
    tokens_out: int


class Clients:
    def __init__(self, *, max_retries: int = 2) -> None:
        # The service retries little so throttling (F7) stays visible; evals retry more.
        self.foundry_endpoint = os.environ["FOUNDRY_ENDPOINT"].rstrip("/")
        self.search_endpoint = os.environ["SEARCH_ENDPOINT"].rstrip("/")
        self.gateway_url = os.environ.get("CHAT_GATEWAY_URL", "").rstrip("/")
        self.credential = DefaultAzureCredential()
        self._openai: AsyncOpenAI = make_openai_client(
            self.foundry_endpoint, credential=self.credential, max_retries=max_retries
        )
        self._gateway: AsyncOpenAI | None = None
        self._http = httpx.AsyncClient(timeout=60)

    def chat_client(self, route: str) -> AsyncOpenAI:
        if route == "gateway":
            if not self.gateway_url:
                raise RuntimeError("chat_route=gateway but CHAT_GATEWAY_URL is not set")
            if self._gateway is None:
                self._gateway = make_openai_client(
                    self.gateway_url, credential=self.credential, max_retries=0
                )
            return self._gateway
        return self._openai

    async def embed(
        self, texts: Sequence[str], deployment: str, dimensions: int
    ) -> list[list[float]]:
        kwargs: dict[str, Any] = {}
        if deployment != "text-embedding-ada-002":
            kwargs["dimensions"] = dimensions
        resp = await self._openai.embeddings.create(model=deployment, input=list(texts), **kwargs)
        return [d.embedding for d in resp.data]

    async def chat(
        self, route: str, deployment: str, system: str, user: str, max_tokens: int
    ) -> ChatResult:
        resp = await self.chat_client(route).chat.completions.create(
            model=deployment,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=max_tokens,
            temperature=0,
        )
        usage = resp.usage
        return ChatResult(
            text=resp.choices[0].message.content or "",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
        )

    async def rerank(
        self, deployment: str, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        token = await self.credential.get_token(COGNITIVE_SCOPE)
        resp = await self._http.post(
            f"{self.foundry_endpoint}/providers/cohere/v2/rerank",
            headers={"Authorization": f"Bearer {token.token}"},
            json={
                "model": deployment,
                "query": query,
                "documents": list(documents),
                "top_n": top_n,
            },
        )
        resp.raise_for_status()
        return [(r["index"], float(r["relevance_score"])) for r in resp.json()["results"]]

    async def close(self) -> None:
        await self._openai.close()
        if self._gateway is not None:
            await self._gateway.close()
        await self._http.aclose()
        await self.credential.close()


__all__ = ["FOUNDRY_SCOPE", "ChatResult", "Clients"]
