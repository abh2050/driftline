"""ops-api (spec 5.1.11): the human-facing API behind the console and the MCP surface.

Every route needs a Driftline API token: viewer (or admin) to read, admin to approve, re-run
or use the demo controls. The console forwards the signed-in user's token; MCP clients reach
the same operations through APIM (`/mcp`).

Live updates: one background loop polls Cosmos for changed incidents and the `incidents`
topic pushes openings immediately; both fan out to every GET /stream client.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from opentelemetry import trace

from driftline_common.auth import Principal, ServiceToken, require_roles
from driftline_common.dapr import DaprPublisher
from driftline_common.telemetry import inject_context, setup_telemetry
from ops_api import views
from ops_api.mcp import mount_mcp
from ops_api.models import Answer, ChatRequest, FaultRequest, ReplayRequest

log = logging.getLogger("ops-api")
tracer = trace.get_tracer("ops-api")
PUBSUB = os.environ.get("PUBSUB_NAME", "pubsub-ops-api")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator")
PATIENT_RAG_URL = os.environ.get("PATIENT_RAG_URL", "http://patient-rag")
REPORTS = Path(os.environ.get("REPORTS_DIR", Path(__file__).resolve().parents[4] / "evals/reports"))
POLL_S = float(os.environ.get("STREAM_POLL_SECONDS", "3"))
FAULTS = [f"F{i}" for i in range(1, 8)]
ARM = "https://management.azure.com"

Viewer = Annotated[Principal, Depends(require_roles("viewer", "admin"))]
Admin = Annotated[Principal, Depends(require_roles("admin"))]


class Hub:
    """Fan-out of incident updates to connected SSE clients."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, item: dict[str, Any]) -> None:
        for q in list(self.clients):
            if q.qsize() < 500:
                q.put_nowait(item)


class State:
    cred: DefaultAzureCredential
    cosmos: CosmosClient
    incidents: Any
    http: httpx.AsyncClient
    token: ServiceToken
    publisher: DaprPublisher
    hub: Hub


st = State()


def who(p: Principal) -> str:
    return p.name or p.subject


async def poll_changes() -> None:
    """Push every incident whose document changed since the last look."""
    last = 0
    while True:
        try:
            q = "SELECT * FROM c WHERE c._ts > @ts"
            async for doc in st.incidents.query_items(
                q, parameters=[{"name": "@ts", "value": last}], partition_key="incident"
            ):
                last = max(last, int(doc["_ts"]))
                st.hub.publish({"type": "incident", "incident": views.summary(doc)})
        except Exception:
            log.exception("incident poll failed")
        await asyncio.sleep(POLL_S)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("ops-api")
    st.cred = DefaultAzureCredential()
    st.cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=st.cred)
    st.incidents = st.cosmos.get_database_client("driftline").get_container_client("incidents")
    st.http = httpx.AsyncClient(timeout=httpx.Timeout(60, read=300))
    st.token = ServiceToken(st.cred)
    st.publisher = DaprPublisher(PUBSUB)
    st.hub = Hub()
    poller = asyncio.create_task(poll_changes())
    async with app.state.mcp_lifespan():
        try:
            yield
        finally:
            poller.cancel()
            await st.publisher.close()
            await st.http.aclose()
            await st.cosmos.close()
            await st.cred.close()
            tel.shutdown()


app = FastAPI(title="ops-api", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "stream_clients": len(st.hub.clients)}


@app.get("/me")
async def me(p: Viewer) -> dict[str, Any]:
    return {"name": who(p), "roles": sorted(p.roles), "admin": "admin" in p.roles}


# ---- incidents ------------------------------------------------------------------------


async def read_incident(incident_id: str) -> dict[str, Any]:
    try:
        doc: dict[str, Any] = await st.incidents.read_item(incident_id, partition_key="incident")
    except Exception as e:
        raise HTTPException(404, "unknown incident") from e
    return doc


LIST_ALL = "SELECT * FROM c ORDER BY c.detected_at DESC OFFSET 0 LIMIT @n"
LIST_WAITING = (
    "SELECT * FROM c WHERE c.status = 'awaiting_human' "
    "ORDER BY c.detected_at DESC OFFSET 0 LIMIT @n"
)


async def list_docs(*, waiting: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    q = LIST_WAITING if waiting else LIST_ALL
    return [
        d
        async for d in st.incidents.query_items(
            q, parameters=[{"name": "@n", "value": limit}], partition_key="incident"
        )
    ]


@app.get("/incidents")
async def incidents(_: Viewer, limit: int = 50) -> list[dict[str, Any]]:
    return [views.summary(d) for d in await list_docs(limit=min(max(limit, 1), 200))]


@app.get("/incidents/{incident_id}")
async def incident(incident_id: str, _: Viewer) -> dict[str, Any]:
    return views.detail(await read_incident(incident_id))


@app.get("/approvals")
async def approvals(_: Viewer) -> list[dict[str, Any]]:
    docs = await list_docs(waiting=True, limit=200)
    return [a for d in docs if (a := views.approval(d))]


async def respond_to(incident_id: str, answer: Answer, p: Principal) -> dict[str, Any]:
    """Approve or reject: the orchestrator resumes the paused workflow (approvals topic)."""
    doc = await read_incident(incident_id)
    if views.approval(doc) is None:
        raise HTTPException(409, "incident is not waiting for a human")
    with tracer.start_as_current_span("ops-api.respond") as span:
        span.set_attribute("driftline.incident_id", incident_id)
        span.set_attribute("enduser.id", who(p))
        sent = await st.publisher.publish(
            "approvals",
            {
                "incident_id": incident_id,
                "approved": answer.approved,
                "by": who(p),
                "note": answer.note,
            },
        )
    if not sent:
        raise HTTPException(503, "event bus unavailable")
    return {"incident_id": incident_id, "approved": answer.approved, "by": who(p)}


@app.post("/incidents/{incident_id}/respond")
async def respond(incident_id: str, answer: Answer, p: Admin) -> dict[str, Any]:
    return await respond_to(incident_id, answer, p)


@app.post("/incidents/{incident_id}/rerun")
async def rerun(incident_id: str, p: Admin) -> dict[str, Any]:
    """Run the workflow again, which re-validates the fix in the sandbox."""
    resp = await st.http.post(
        f"{ORCHESTRATOR_URL}/incidents/{incident_id}/run",
        headers={**await st.token.header(), **inject_context()},
    )
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text[:500])
    log.warning("rerun of %s requested by %s", incident_id, who(p))
    return dict(resp.json())


# ---- live stream ----------------------------------------------------------------------


@app.get("/stream")
async def stream(request: Request, _: Viewer) -> StreamingResponse:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    st.hub.clients.add(queue)

    async def events() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    item = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"event: {item['type']}\ndata: {json.dumps(item, default=str)}\n\n"
        finally:
            st.hub.clients.discard(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/dapr/subscribe")
async def subscribe() -> list[dict[str, str]]:
    return [{"pubsubname": PUBSUB, "topic": "incidents", "route": "/events/incidents"}]


@app.post("/events/incidents")
async def on_incident(request: Request) -> dict[str, str]:
    """Openings and closures arrive here before the next poll sees them."""
    envelope = await request.json()
    data = envelope.get("data", envelope)
    if isinstance(data.get("incident"), dict):
        st.hub.publish(
            {
                "type": "incident",
                "event": data.get("event"),
                "incident": views.summary(data["incident"]),
            }
        )
    return {"status": "SUCCESS"}


# ---- chat -----------------------------------------------------------------------------


@app.post("/chat")
async def chat(req: ChatRequest, p: Viewer) -> StreamingResponse:
    """Stream the explainer agent's answer (it runs in the orchestrator)."""
    headers = {**await st.token.header(), **inject_context()}

    async def relay() -> AsyncIterator[bytes]:
        with tracer.start_as_current_span("ops-api.chat") as span:
            span.set_attribute("enduser.id", who(p))
            async with st.http.stream(
                "POST", f"{ORCHESTRATOR_URL}/chat", json=req.model_dump(), headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    yield f"[chat unavailable: HTTP {resp.status_code}]".encode()
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/plain; charset=utf-8")


# ---- router report --------------------------------------------------------------------


def latest_router_report() -> dict[str, Any] | None:
    reports = sorted(REPORTS.glob("*-router.json"))
    if not reports:
        return None
    data: dict[str, Any] = json.loads(reports[-1].read_text())
    data["report"] = reports[-1].name
    return data


@app.get("/router/report")
async def router_report(_: Viewer) -> dict[str, Any]:
    data = await asyncio.to_thread(latest_router_report)
    if data is None:
        raise HTTPException(404, "no router report")
    return data


# ---- demo controls (admin) ------------------------------------------------------------


@app.post("/demo/fault")
async def demo_fault(req: FaultRequest, p: Admin) -> dict[str, Any]:
    if req.fault_id not in FAULTS:
        raise HTTPException(400, f"fault must be one of {FAULTS}")
    return await patient_rag("POST", "/admin/fault", {"fault_id": req.fault_id}, p)


@app.post("/demo/reset")
async def demo_reset(p: Admin) -> dict[str, Any]:
    return await patient_rag("POST", "/admin/reset", None, p)


@app.get("/demo/state")
async def demo_state(p: Admin) -> dict[str, Any]:
    return await patient_rag("GET", "/state", None, p)


async def patient_rag(
    method: str, path: str, body: dict[str, Any] | None, p: Principal
) -> dict[str, Any]:
    with tracer.start_as_current_span("ops-api.demo") as span:
        span.set_attribute("enduser.id", who(p))
        span.set_attribute("http.route", path)
        resp = await st.http.request(
            method,
            f"{PATIENT_RAG_URL}{path}",
            json=body,
            headers={**await st.token.header(), **inject_context()},
        )
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text[:500])
    log.warning("demo %s %s by %s", method, path, who(p))
    return dict(resp.json())


@app.post("/demo/replay")
async def demo_replay(req: ReplayRequest, p: Admin) -> dict[str, Any]:
    """Start a replay job execution for one fault trial (accelerated, its own stream)."""
    if req.fault_id not in FAULTS:
        raise HTTPException(400, f"fault must be one of {FAULTS}")
    job = os.environ.get("REPLAY_JOB_ID")
    if not job:
        raise HTTPException(503, "replay job not configured")
    run_id = f"demo-{req.fault_id.lower()}-{uuid.uuid4().hex[:6]}"
    token = (await st.cred.get_token(f"{ARM}/.default")).token
    auth = {"Authorization": f"Bearer {token}"}
    current = await st.http.get(f"{ARM}{job}?api-version=2025-01-01", headers=auth)
    current.raise_for_status()
    container = current.json()["properties"]["template"]["containers"][0]
    env = [e for e in container.get("env", []) if e["name"] not in OVERRIDES]
    env += [
        {"name": "REPLAY_PLAN", "value": f"fault:{req.fault_id}:{req.trial}"},
        {"name": "REPLAY_RUN_ID", "value": run_id},
        {"name": "REPLAY_SPEED", "value": "1440"},
    ]
    # an execution template replaces the container's settings, so pass them all back
    resp = await st.http.post(
        f"{ARM}{job}/start?api-version=2025-01-01",
        headers=auth,
        json={"containers": [{**container, "env": env}]},
    )
    if resp.status_code >= 400:
        raise HTTPException(502, f"replay start failed: {resp.text[:300]}")
    log.warning("replay %s started by %s", run_id, who(p))
    return {"stream": f"replay-{run_id}", "execution": resp.json().get("name"), "by": who(p)}


OVERRIDES = {"REPLAY_PLAN", "REPLAY_RUN_ID", "REPLAY_SPEED"}


# ---- browser traces -------------------------------------------------------------------


@app.post("/v1/traces")
async def browser_traces(request: Request, _: Viewer) -> Response:
    """Forward the console's OTLP/HTTP spans to the collector (spec 5.2.4)."""
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "trace batch too large")
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return Response(status_code=204)
    ctype = request.headers.get("content-type", "application/json")
    resp = await st.http.post(
        f"{endpoint}/v1/traces", content=body, headers={"Content-Type": ctype}
    )
    return Response(content=resp.content, status_code=resp.status_code, media_type=ctype)


mount_mcp(app, respond_to, list_docs, read_incident)
