"""Calls the router service (every decision goes through it, spec 4.1)."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from driftline_common.auth import ServiceToken
from driftline_common.telemetry import inject_context


class RouterClient:
    def __init__(self, token: ServiceToken) -> None:
        self.url = os.environ.get("ROUTER_URL", "http://router")
        self.token = token
        self.http = httpx.AsyncClient(timeout=120)

    async def decide(
        self, decision_type: str, context: dict[str, Any], incident_id: str, **extra: Any
    ) -> dict[str, Any]:
        body = {
            "decision_type": decision_type,
            "context": context,
            "incident_id": incident_id,
            "question_id": f"{decision_type.lower()}-{incident_id}-{uuid.uuid4().hex[:6]}",
            **extra,
        }
        resp = await self.http.post(
            f"{self.url}/decide",
            json=body,
            headers={**await self.token.header(), **inject_context()},
        )
        resp.raise_for_status()
        out: dict[str, Any] = resp.json()
        return out

    async def close(self) -> None:
        await self.http.aclose()
