"""Write one row per decision to the Log Analytics table DriftlineDecisions_CL (spec 4.1)
through the logs-ingestion DCR, authenticated with the router's managed identity."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.monitor.ingestion.aio import LogsIngestionClient

from driftline_common.decision import Decision

log = logging.getLogger("router.sink")


def row(d: Decision, *, incident_id: str | None, trace_id: str) -> dict[str, Any]:
    return {
        "TimeGenerated": datetime.now(UTC).isoformat(),
        "DecisionType": d.decision_type,
        "QuestionId": d.question_id,
        "Client": d.client,
        "Model": d.model,
        "Options": d.options,
        "Scores": d.scores,
        "Chosen": d.chosen,
        "Margin": d.margin,
        "Escalated": d.escalated,
        "LatencyMs": d.latency_ms,
        "InputTokens": d.input_tokens,
        "CostUsd": d.cost_usd,
        "IncidentId": incident_id or "",
        "TraceId": trace_id,
    }


class DecisionSink:
    def __init__(self) -> None:
        endpoint = os.environ.get("DCE_LOGS_ENDPOINT")
        self.rule_id = os.environ.get("DECISIONS_DCR_IMMUTABLE_ID", "")
        self.stream = os.environ.get("DECISIONS_STREAM", "Custom-DriftlineDecisions")
        self.enabled = bool(endpoint and self.rule_id)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
        self._credential = DefaultAzureCredential() if self.enabled else None
        self._client = (
            LogsIngestionClient(endpoint, self._credential)  # type: ignore[arg-type]
            if self.enabled
            else None
        )
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._drain())

    def put(self, r: dict[str, Any]) -> None:
        if self.enabled:
            try:
                self._queue.put_nowait(r)
            except asyncio.QueueFull:
                log.warning("decision sink queue full; dropping row")

    async def _drain(self) -> None:
        if self._client is None:
            return
        while True:
            batch: list[MutableMapping[str, Any]] = [await self._queue.get()]
            await asyncio.sleep(2)
            while not self._queue.empty() and len(batch) < 500:
                batch.append(self._queue.get_nowait())
            try:
                await self._client.upload(rule_id=self.rule_id, stream_name=self.stream, logs=batch)
            except Exception:
                log.exception("decision sink upload failed (%d rows)", len(batch))

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.close()
        if self._credential:
            await self._credential.close()
