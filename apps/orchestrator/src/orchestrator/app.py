"""orchestrator (spec 5.1.7): runs the incident workflow for every new incident.

Dapr subscriptions:
  incidents    an opened incident starts a workflow (one per incident, Cosmos checkpoints)
  validations  sandbox-runner results resolve the waiting validate step
  approvals    a human's answer (from ops-api) resumes a paused workflow
HTTP: status, manual rerun, and /respond (the same resume path, for operators and tests).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from opentelemetry import context, trace
from pydantic import BaseModel

from driftline_common.audit import AuditWriter
from driftline_common.auth import Principal, ServiceToken, require_roles
from driftline_common.dapr import DaprPublisher
from driftline_common.pricing import Pricing
from driftline_common.telemetry import extract_context, setup_telemetry
from orchestrator.agents import AgentFactory
from orchestrator.checkpoints import CosmosCheckpointStorage
from orchestrator.identities import AgentIdentities
from orchestrator.models import HumanResponse, IncidentRun
from orchestrator.router_client import RouterClient
from orchestrator.safety import PromptShield, shield_middleware
from orchestrator.tools import mcp_tool
from orchestrator.workflow import build_workflow, deps

log = logging.getLogger("orchestrator")
tracer = trace.get_tracer("orchestrator")
PUBSUB = os.environ.get("PUBSUB_NAME", "pubsub-orchestrator")
# answering a human gate or re-running a workflow is an operator action (ops-api)
Operator = Annotated[Principal, Depends(require_roles("ops.write"))]


class ChatRequest(BaseModel):
    question: str
    incident_id: str | None = None


class Answer(BaseModel):
    approved: bool
    by: str
    note: str = ""


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
    deps.shield = PromptShield(cred)
    deps.publisher = DaprPublisher(PUBSUB)
    deps.service_token = token
    deps.validations = {}
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
        for t in running.values():
            t.cancel()
        await deps.router.close()
        await deps.identities.close()
        await deps.audit.close()
        await deps.shield.close()
        await deps.publisher.close()
        await cosmos.close()
        await cred.close()
        tel.shutdown()


app = FastAPI(title="orchestrator", lifespan=lifespan)


async def _fail(incident_id: str, e: Exception) -> None:
    await deps.audit.write(
        incident_id, "workflow", "orchestrator", "failed", {"error": str(e)[:1000]}
    )
    doc = await deps.incidents.read_item(incident_id, partition_key="incident")
    doc["status"] = "failed"
    doc["error"] = str(e)[:1000]
    await deps.incidents.upsert_item(doc)


async def _record_pause(incident_id: str, result: Any) -> None:
    requests = result.get_request_info_events()
    if requests:
        doc = await deps.incidents.read_item(incident_id, partition_key="incident")
        doc["pending_request_id"] = requests[0].request_id
        await deps.incidents.upsert_item(doc)


async def run_workflow(incident: dict[str, Any]) -> None:
    run = IncidentRun(incident=incident)
    with tracer.start_as_current_span("orchestrator.workflow") as span:
        span.set_attribute("driftline.incident_id", run.id)
        try:
            workflow = build_workflow(app.state.checkpoints, run.id)
            result = await workflow.run(run)
            await _record_pause(run.id, result)
        except Exception as e:
            log.exception("workflow %s failed", run.id)
            span.set_status(trace.StatusCode.ERROR, str(e))
            await _fail(run.id, e)
        finally:
            app.state.running.pop(run.id, None)


async def resume(incident_id: str, answer: Answer) -> dict[str, Any]:
    doc = await deps.incidents.read_item(incident_id, partition_key="incident")
    request_id = doc.get("pending_request_id")
    if not request_id or doc.get("status") != "awaiting_human":
        raise HTTPException(409, "incident is not waiting for a human")
    with tracer.start_as_current_span("orchestrator.resume") as span:
        span.set_attribute("driftline.incident_id", incident_id)
        storage = app.state.checkpoints
        checkpoint = await storage.get_latest(workflow_name=f"driftline-{incident_id}")
        if checkpoint is None:
            raise HTTPException(409, "no checkpoint to resume from")
        workflow = build_workflow(storage, incident_id)
        result = await workflow.run(
            responses={request_id: HumanResponse(answer.approved, answer.by, answer.note)},
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_storage=storage,
        )
        outputs = result.get_outputs()
    return {"resumed": True, "status": getattr(outputs[-1], "status", None) if outputs else None}


def start(incident: dict[str, Any]) -> bool:
    if incident["id"] in app.state.running:
        return False
    app.state.running[incident["id"]] = asyncio.create_task(run_workflow(incident))
    return True


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "status": "ok",
        "running": sorted(app.state.running),
        "waiting_validations": sorted(deps.validations),
    }


@app.get("/dapr/subscribe")
async def subscribe() -> list[dict[str, str]]:
    return [
        {"pubsubname": PUBSUB, "topic": "incidents", "route": "/events/incidents"},
        {"pubsubname": PUBSUB, "topic": "validations", "route": "/events/validations"},
        {"pubsubname": PUBSUB, "topic": "approvals", "route": "/events/approvals"},
    ]


async def _envelope(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = await request.json()
    return envelope, envelope.get("data", envelope)


@app.post("/events/incidents")
async def on_incident(request: Request) -> dict[str, str]:
    envelope, data = await _envelope(request)
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


@app.post("/events/validations")
async def on_validation(request: Request) -> dict[str, str]:
    _, data = await _envelope(request)
    fut = deps.validations.get(str(data.get("run_id")))
    if fut is not None and not fut.done():
        fut.set_result(data)
    return {"status": "SUCCESS"}


@app.post("/events/approvals")
async def on_approval(request: Request) -> dict[str, str]:
    envelope, data = await _envelope(request)
    token = context.attach(extract_context(envelope))
    try:
        await resume(
            str(data["incident_id"]),
            Answer(
                approved=bool(data["approved"]),
                by=str(data.get("by", "?")),
                note=str(data.get("note", "")),
            ),
        )
    except HTTPException as e:
        log.warning("approval for %s ignored: %s", data.get("incident_id"), e.detail)
    finally:
        context.detach(token)
    return {"status": "SUCCESS"}


@app.post("/incidents/{incident_id}/respond")
async def respond(incident_id: str, answer: Answer, _: Operator) -> dict[str, Any]:
    return await resume(incident_id, answer)


@app.post("/incidents/{incident_id}/run")
async def rerun(incident_id: str, _: Operator) -> dict[str, Any]:
    """Re-run the workflow for a stored incident (operators and evals)."""
    try:
        doc = await deps.incidents.read_item(incident_id, partition_key="incident")
    except Exception as e:
        raise HTTPException(404, "unknown incident") from e
    return {"started": start({k: v for k, v in doc.items() if not k.startswith("_")})}


@app.post("/chat")
async def chat(req: ChatRequest, who: Operator) -> StreamingResponse:
    """The explainer agent, streamed as plain text (spec 5.2e, through ops-api)."""
    record: dict[str, Any] = {}
    if req.incident_id:
        try:
            doc = await deps.incidents.read_item(req.incident_id, partition_key="incident")
            record = {k: v for k, v in doc.items() if not k.startswith("_")}
        except Exception as e:
            raise HTTPException(404, "unknown incident") from e
    identity = deps.factory.identity("explainer")
    token = await deps.identities.token(identity)
    prompt = req.question
    if record:
        prompt = (
            f"Incident record:\n{json.dumps(record, default=str)[:20000]}\n\nQuestion: {prompt}"
        )

    async def stream() -> AsyncIterator[str]:
        tools = [mcp_tool(t, token) for t in deps.factory.cfg.agents["explainer"].tools]
        with tracer.start_as_current_span("orchestrator.chat") as span:
            span.set_attribute("gen_ai.agent.name", "explainer")
            if req.incident_id:
                span.set_attribute("driftline.incident_id", req.incident_id)
            try:
                for t in tools:
                    await t.__aenter__()
                agent = deps.factory.build(
                    "explainer",
                    tools,
                    middleware=[
                        shield_middleware(
                            deps.shield, deps.audit, req.incident_id or "chat", identity
                        )
                    ],
                )
                async for update in agent.run(prompt, stream=True):
                    if update.text:
                        yield update.text
            except Exception as e:
                log.exception("chat failed")
                yield f"\n\n[explainer error: {type(e).__name__}]"
            finally:
                for t in tools:
                    await t.close()

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")
