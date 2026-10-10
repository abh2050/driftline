"""ingest (spec 5.1.4): subscribes to `telemetry` through Dapr, validates each event,
aggregates one-minute windows by event time and upserts them into Cosmos `windows`."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, Request
from opentelemetry import context, trace
from pydantic import ValidationError

from driftline_common.schemas import TelemetryEvent
from driftline_common.telemetry import extract_context, setup_telemetry
from ingest.windows import WindowAggregator

log = logging.getLogger("ingest")
tracer = trace.get_tracer("ingest")
PUBSUB = os.environ.get("PUBSUB_NAME", "pubsub-ingest")
FLUSH_SECONDS = 15


async def flush_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        await flush(app)


async def flush(app: FastAPI, *, force: bool = False) -> int:
    windows = app.state.agg.flush(force=force)
    for w in windows:
        try:
            await app.state.windows.upsert_item(w.model_dump(mode="json"))
        except Exception:
            log.exception("window upsert failed: %s", w.id)
    app.state.stats["windows"] += len(windows)
    return len(windows)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("ingest")
    cred = DefaultAzureCredential()
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred)
    app.state.windows = cosmos.get_database_client("driftline").get_container_client("windows")
    app.state.agg = WindowAggregator()
    app.state.stats = {"events": 0, "invalid": 0, "windows": 0}
    task = asyncio.create_task(flush_loop(app))
    try:
        yield
    finally:
        task.cancel()
        await flush(app, force=True)
        await cosmos.close()
        await cred.close()
        tel.shutdown()


app = FastAPI(title="ingest", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", **app.state.stats, "pending": app.state.agg.pending()}


@app.get("/dapr/subscribe")
async def subscribe() -> list[dict[str, str]]:
    return [{"pubsubname": PUBSUB, "topic": "telemetry", "route": "/events/telemetry"}]


@app.post("/events/telemetry")
async def on_telemetry(request: Request) -> dict[str, str]:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    token = context.attach(extract_context(envelope))
    try:
        with tracer.start_as_current_span("ingest.telemetry"):
            try:
                event = TelemetryEvent.model_validate(data)
            except ValidationError:
                app.state.stats["invalid"] += 1
                # DROP: invalid events go nowhere useful; tell Dapr not to retry them.
                return {"status": "DROP"}
            app.state.agg.add(event)
            app.state.stats["events"] += 1
    finally:
        context.detach(token)
    return {"status": "SUCCESS"}
