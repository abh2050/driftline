"""patient-rag service (spec 5.1.1).

POST /ask              answer a question (role patientrag.query); publishes one telemetry
                       event to the `telemetry` topic through Dapr
GET  /state            active fault, config hash, source/index versions (any caller in env)
POST /admin/fault      apply a fault from faults/<id>.yaml (role patientrag.admin)
POST /admin/reset      back to baseline (role patientrag.admin)
POST /admin/indexer    enable or disable the indexer (role patientrag.admin)
POST /admin/reindex    rebuild the index from the current source version (role patientrag.admin)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from pydantic import BaseModel, Field

from driftline_common.auth import Principal, require_roles
from driftline_common.dapr import DaprPublisher
from driftline_common.pricing import Pricing
from driftline_common.schemas import Persona, TelemetryEvent
from driftline_common.telemetry import setup_telemetry
from patient_rag.clients import Clients
from patient_rag.config import APP_DIR, RagConfig, apply, load_base_config, load_fault
from patient_rag.grounding import GroundednessJudge
from patient_rag.index import build_index
from patient_rag.pipeline import RagPipeline, RagResult

log = logging.getLogger("patient-rag")
tracer = trace.get_tracer("patient-rag")

Query = Annotated[Principal, Depends(require_roles("patientrag.query", "patientrag.admin"))]
Admin = Annotated[Principal, Depends(require_roles("patientrag.admin"))]


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    persona: Persona = "synthetic"
    query_id: str | None = None
    # Canary requests: any of these strings in the answer counts as correct.
    expected: list[str] | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[str]
    doc_ids: list[str]
    latency_ms: float
    config_hash: str
    error: str | None = None


class FaultRequest(BaseModel):
    fault_id: str


class IndexerRequest(BaseModel):
    enabled: bool


class State:
    def __init__(self, clients: Clients) -> None:
        self.clients = clients
        self.base = load_base_config()
        self.fault_id: str | None = None
        self.cfg: RagConfig = self.base
        self.source_version = 1  # what the document source currently holds
        self.index_version = 1  # what the serving index was built from
        self.indexer_enabled = True
        self.reindexing = False
        self.pipeline = RagPipeline(clients, self.cfg)

    async def set_config(self, cfg: RagConfig) -> None:
        old = self.pipeline
        self.cfg = cfg
        self.pipeline = RagPipeline(self.clients, cfg)
        await old.close()

    def snapshot(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "config_hash": self.cfg.config_hash(),
            "config": self.cfg.model_dump(),
            "index_name": self.cfg.index_name(),
            "source_version": self.source_version,
            "index_version": self.index_version,
            "indexer_enabled": self.indexer_enabled,
            "reindexing": self.reindexing,
        }


GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")


def build_event(
    req: AskRequest, r: RagResult, groundedness: float | None, pricing: Pricing, cfg: RagConfig
) -> TelemetryEvent:
    top = r.hits[0].score if r.hits else None
    mean = sum(h.score for h in r.hits) / len(r.hits) if r.hits else None
    cost = pricing.chat(cfg.chat_deployment, r.tokens_in, r.tokens_out)
    cost += pricing.chat(cfg.embedding_deployment, r.embedding_tokens, 0)
    if r.reranked:
        cost += pricing.rerank(cfg.reranker_deployment, 1)
    canary = None
    if req.expected:
        canary = any(e.lower() in r.answer.lower() for e in req.expected)
    status = (
        200 if r.error is None else (429 if "429" in r.error or "RateLimit" in r.error else 500)
    )
    return TelemetryEvent(
        event_id=str(uuid.uuid4()),
        ts=datetime.now(UTC),
        persona=req.persona,
        query_id=req.query_id,
        latency_ms=round(r.latency_ms, 1),
        retrieval_latency_ms=round(r.retrieval_latency_ms, 1),
        llm_latency_ms=round(r.llm_latency_ms, 1),
        top_score=None if top is None else round(top, 4),
        mean_score=None if mean is None else round(mean, 4),
        hit_count=len(r.hits),
        citation_count=len(r.citations),
        groundedness=groundedness,
        error=r.error is not None,
        status_code=status,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        cost_usd=round(cost, 7),
        config_hash=r.config_hash,
        prompt_version=r.prompt_version,
        git_commit=GIT_COMMIT,
        canary_correct=canary,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("patient-rag")
    HTTPXClientInstrumentor().instrument()
    clients = Clients()
    app.state.s = State(clients)
    app.state.publisher = DaprPublisher(os.environ.get("PUBSUB_NAME", "pubsub-patient-rag"))
    app.state.judge = GroundednessJudge(clients.foundry_endpoint, app.state.s.cfg.judge_deployment)
    app.state.pricing = Pricing.load()
    app.state.background = set()
    try:
        yield
    finally:
        await app.state.s.pipeline.close()
        await app.state.publisher.close()
        await clients.close()
        tel.shutdown()


app = FastAPI(title="patient-rag", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/configs")
async def configs() -> dict[str, Any]:
    """Every config patient-rag can run (baseline and each fault), keyed by config hash.

    The detector uses this registry to turn config hashes into a config diff.
    """
    base = load_base_config()
    out = {base.config_hash(): {"label": "baseline", "config": base.model_dump()}}
    for fault in sorted(p.stem for p in (APP_DIR / "faults").glob("*.yaml")):
        cfg = apply(base, load_fault(fault).overrides)
        out.setdefault(cfg.config_hash(), {"label": fault, "config": cfg.model_dump()})
    return out


@app.get("/state")
async def get_state() -> dict[str, Any]:
    return dict(app.state.s.snapshot())


async def _publish(req: AskRequest, r: RagResult, cfg: RagConfig, sample: bool) -> None:
    groundedness = None
    if sample and r.error is None:
        with tracer.start_as_current_span("patient-rag.groundedness"):
            groundedness = await app.state.judge.score(req.query, r.answer, r.context)
    event = build_event(req, r, groundedness, app.state.pricing, cfg)
    with tracer.start_as_current_span("patient-rag.publish_telemetry"):
        try:
            await app.state.publisher.publish("telemetry", event.model_dump(mode="json"))
        except Exception:
            log.exception("telemetry publish failed")


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, _: Query) -> AskResponse:
    s: State = app.state.s
    cfg, pipeline = s.cfg, s.pipeline
    with tracer.start_as_current_span("patient-rag.ask") as span:
        span.set_attribute("driftline.persona", req.persona)
        span.set_attribute("driftline.config_hash", cfg.config_hash())
        span.set_attribute("gen_ai.request.model", cfg.chat_deployment)
        result = await pipeline.ask(req.query)
        span.set_attribute("gen_ai.usage.input_tokens", result.tokens_in)
        span.set_attribute("gen_ai.usage.output_tokens", result.tokens_out)
        if result.error:
            span.set_status(trace.StatusCode.ERROR, result.error)
        sample = random.random() < cfg.groundedness_sample_rate  # noqa: S311
        task = asyncio.create_task(_publish(req, result, cfg, sample))
        app.state.background.add(task)
        task.add_done_callback(app.state.background.discard)
    return AskResponse(
        answer=result.answer,
        citations=result.citations,
        doc_ids=result.retrieved_doc_ids,
        latency_ms=result.latency_ms,
        config_hash=result.config_hash,
        error=result.error,
    )


@app.post("/admin/fault")
async def inject_fault(req: FaultRequest, who: Admin) -> dict[str, Any]:
    s: State = app.state.s
    try:
        fault = load_fault(req.fault_id)
    except KeyError as e:
        raise HTTPException(404, f"unknown fault {req.fault_id}") from e
    await s.set_config(apply(s.base, fault.overrides))
    s.fault_id = fault.id
    if "indexer_enabled" in fault.state:
        s.indexer_enabled = bool(fault.state["indexer_enabled"])
    if "source_version" in fault.state:
        s.source_version = int(fault.state["source_version"])
    log.warning("fault %s applied by %s", fault.id, who.name or who.subject)
    return dict(s.snapshot())


@app.post("/admin/reset")
async def reset(who: Admin) -> dict[str, Any]:
    s: State = app.state.s
    await s.set_config(s.base)
    s.fault_id = None
    s.indexer_enabled = True
    s.source_version = 1
    if s.index_version != 1:
        await _reindex(s)
    log.warning("reset to baseline by %s", who.name or who.subject)
    return dict(s.snapshot())


@app.post("/admin/indexer")
async def set_indexer(req: IndexerRequest, _: Admin) -> dict[str, Any]:
    app.state.s.indexer_enabled = req.enabled
    return dict(app.state.s.snapshot())


async def _reindex(s: State) -> None:
    s.reindexing = True
    try:
        with tracer.start_as_current_span("patient-rag.reindex"):
            await build_index(s.clients, s.cfg, version=s.source_version)
        s.index_version = s.source_version
    finally:
        s.reindexing = False


@app.post("/admin/reindex")
async def reindex(_: Admin) -> dict[str, Any]:
    s: State = app.state.s
    if not s.indexer_enabled:
        raise HTTPException(409, "indexer is disabled")
    if s.reindexing:
        raise HTTPException(409, "reindex already running")
    task = asyncio.create_task(_reindex(s))
    app.state.background.add(task)
    task.add_done_callback(app.state.background.discard)
    return dict(s.snapshot())
