"""Azure AI Content Safety prompt shields over everything agents read (spec 5.1.7).

Tool results (file contents, query rows, retrieved documents) are checked as `documents`
before the model sees them. A detected indirect injection replaces the result with a notice
and writes an audit record, so the blocked content never reaches the agent.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from agent_framework import function_middleware
from azure.core.credentials_async import AsyncTokenCredential

from driftline_common.audit import AuditWriter

log = logging.getLogger("orchestrator.safety")
SCOPE = "https://cognitiveservices.azure.com/.default"
API_VERSION = "2024-09-01"
CHUNK = 9000  # stay under the per-document limit
BLOCKED = (
    "[blocked by Driftline content safety: this tool result contained instructions "
    "aimed at AI agents (indirect prompt injection) and was withheld]"
)


class PromptShield:
    def __init__(self, credential: AsyncTokenCredential, endpoint: str | None = None) -> None:
        self.endpoint = (endpoint or os.environ.get("CONTENT_SAFETY_ENDPOINT", "")).rstrip("/")
        self.credential = credential
        self.http = httpx.AsyncClient(timeout=30)

    async def attack_detected(self, text: str) -> bool:
        if not self.endpoint or not text.strip():
            return False
        token = (await self.credential.get_token(SCOPE)).token
        docs = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
        for start in range(0, len(docs), 5):
            resp = await self.http.post(
                f"{self.endpoint}/contentsafety/text:shieldPrompt",
                params={"api-version": API_VERSION},
                headers={"Authorization": f"Bearer {token}"},
                json={"userPrompt": "", "documents": docs[start : start + 5]},
            )
            resp.raise_for_status()
            if any(d.get("attackDetected") for d in resp.json().get("documentsAnalysis", [])):
                return True
        return False

    async def close(self) -> None:
        await self.http.aclose()


def result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        return "\n".join(str(getattr(c, "text", "") or "") for c in result)
    return str(getattr(result, "text", "") or result)


def shield_middleware(
    shield: PromptShield, audit: AuditWriter, incident_id: str, actor: str
) -> Callable[[Any, Callable[[], Awaitable[None]]], Awaitable[None]]:
    @function_middleware
    async def middleware(context: Any, call_next: Callable[[], Awaitable[None]]) -> None:
        await call_next()
        text = result_text(context.result)
        try:
            detected = await shield.attack_detected(text)
        except httpx.HTTPError:
            log.exception("prompt shield unavailable; withholding the tool result")
            detected = True  # fail closed
        if detected:
            await audit.write(
                incident_id,
                "prompt-shield",
                actor,
                "blocked",
                {"tool": getattr(context.function, "name", "?"), "excerpt": text[:300]},
            )
            context.result = BLOCKED

    return middleware
