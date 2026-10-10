"""Publish to Service Bus topics through the app's Dapr sidecar (spec 5.1).

The current W3C trace context is passed as `traceparent`/`tracestate` headers; Dapr puts it
into the CloudEvent and the Service Bus message, so consumers continue the same trace.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from driftline_common.telemetry import inject_context


class DaprPublisher:
    def __init__(self, pubsub: str, client: httpx.AsyncClient | None = None) -> None:
        port = os.environ.get("DAPR_HTTP_PORT")
        self.enabled = port is not None
        self.pubsub = pubsub
        self._base = f"http://127.0.0.1:{port}/v1.0/publish/{pubsub}"
        self._client = client or httpx.AsyncClient(timeout=10)

    async def publish(self, topic: str, data: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        headers = dict(inject_context())
        resp = await self._client.post(f"{self._base}/{topic}", json=data, headers=headers)
        resp.raise_for_status()
        return True

    async def close(self) -> None:
        await self._client.aclose()
