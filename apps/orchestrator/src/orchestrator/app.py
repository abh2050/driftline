"""orchestrator (spec 5.1.7): runs the incident workflow for every new incident.

Subscribes to `incidents` through Dapr. Each opened incident starts a Microsoft Agent
Framework workflow with Cosmos checkpoints; the HTTP API exposes status and a manual rerun.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import context, trace

from driftline_common.audit import AuditWriter
from driftline_common.auth import ServiceToken
from driftline_common.pricing import Pricing
from driftline_common.telemetry import extract_context, setup_telemetry
from orchestrator.agents import AgentFactory
from orchestrator.checkpoints import CosmosCheckpointStorage
from orchestrator.identities import AgentIdentities
from orchestrator.models import IncidentRun
from orchestrator.router_client import RouterClient
from orchestrator.workflow import build_workflow, deps

log = logging.getLogger("orchestrator")
tracer = trace.get_tracer("orchestrator")
PUBSUB = os.environ.get("PUBSUB_NAME", "pubsub-orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("orchestrator")
    cred = DefaultAzureCredential()
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred)
    db = cosmos.get_database_client("driftline")
    token = ServiceToken(cred)
    deps.factory = AgentFactory(cred)
    deps.identities = AgentIdentities()
    deps.router = RouterClient(token)
    deps.audit = AuditWriter(credential=cred)
    deps.incidents = db.get_container_client("incidents")
    deps.pricing = Pricing.load()
    app.state.checkpoints = CosmosCheckpointStorage(db.get_container_client("checkpoints"))
    running: dict[str, asyncio.Task[Any]] = {}
    app.state.running = running
    try:
        await deps.factory.register_all()
    except Exception:
        log.exception("Foundry agent registration failed")
    try:
        yield
    finally:
        for t in app.state.running.values():
            t.cancel()
        await deps.router.close()
        await deps.identities.close()
        await deps.audit.close()
        await cosmos.close()
        await cred.close()
        tel.shutdown()


app = FastAPI(title="orchestrator", lifespan=lifespan)


async def run_workflow(incident: dict[str, Any]) -> None:
    run = IncidentRun(incident=incident)
    with tracer.start_as_current_span("orchestrator.workflow") as span:
        span.set_attribute("driftline.incident_id", run.id)
        try:
            workflow = build_workflow(app.state.checkpoints)
            result = await workflow.run(run)
            outputs = result.get_outputs()
            log.warning(
                "workflow %s finished: %s", run.id, [getattr(o, "status", o) for o in outputs]
            )
        except Exception as e:
            log.exception("workflow %s failed", run.id)
            span.set_status(trace.StatusCode.ERROR, str(e))
            await deps.audit.write(
                run.id, "workflow", "orchestrator", "failed", {"error": str(e)[:1000]}
            )
            doc = await deps.incidents.read_item(run.id, partition_key="incident")
            doc["status"] = "failed"
            doc["error"] = str(e)[:1000]
            await deps.incidents.upsert_item(doc)
        finally:
            app.state.running.pop(run.id, None)


def start(incident: dict[str, Any]) -> bool:
    if incident["id"] in app.state.running:
        return False
    app.state.running[incident["id"]] = asyncio.create_task(run_workflow(incident))
    return True


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "running": sorted(app.state.running)}


@app.get("/dapr/subscribe")
async def subscribe() -> list[dict[str, str]]:
    return [{"pubsubname": PUBSUB, "topic": "incidents", "route": "/events/incidents"}]


@app.post("/events/incidents")
async def on_incident(request: Request) -> dict[str, str]:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    token = context.attach(extract_context(envelope))
    try:
        if data.get("event") == "opened":
            started = start(data["incident"])
            log.warning(
                "incident %s opened (workflow started: %s)", data["incident"]["id"], started
            )
    finally:
        context.detach(token)
    return {"status": "SUCCESS"}


@app.post("/incidents/{incident_id}/run")
async def rerun(incident_id: str) -> dict[str, Any]:
    """Re-run the workflow for a stored incident (operators and evals)."""
    try:
        doc = await deps.incidents.read_item(incident_id, partition_key="incident")
    except Exception as e:
        raise HTTPException(404, "unknown incident") from e
    return {"started": start({k: v for k, v in doc.items() if not k.startswith("_")})}
