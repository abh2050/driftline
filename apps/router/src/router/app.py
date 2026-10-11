"""Router service (spec 5.1.6): POST /decide serves every D1-D6 decision.

Measures latency and cost in one place, escalates when the top score is below
`escalation_threshold`, emits a `decision.<question_id>` span with every Decision field,
and writes one DriftlineDecisions_CL row per decision.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from driftline_common.auth import Principal, require_roles
from driftline_common.config import load_router_config
from driftline_common.decision import CONTENT_FILTERED, DecideRequest, Decision, DecisionClient
from driftline_common.foundry import make_openai_client
from driftline_common.pricing import Pricing
from driftline_common.telemetry import setup_telemetry
from router.clients import build_clients
from router.sink import DecisionSink, row

tracer = trace.get_tracer("router")
Invoker = Annotated[Principal, Depends(require_roles("router.invoke"))]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("router")
    cfg = load_router_config()
    openai = make_openai_client(max_retries=4)
    app.state.cfg = cfg
    app.state.clients = build_clients(cfg, openai, Pricing.load())
    app.state.sink = DecisionSink()
    app.state.sink.start()
    try:
        yield
    finally:
        await app.state.sink.close()
        await openai.close()
        tel.shutdown()


app = FastAPI(title="router", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/clients")
async def clients() -> dict[str, Any]:
    cfg = app.state.cfg
    return {
        "active": cfg.active,
        "clients": sorted(app.state.clients),
        "escalation_threshold": cfg.escalation_threshold,
        "dual_route_margin": cfg.dual_route_margin,
    }


@app.post("/decide", response_model=Decision)
async def decide(req: DecideRequest, who: Invoker) -> Decision:
    cfg = app.state.cfg
    name = req.client or cfg.active
    client: DecisionClient | None = app.state.clients.get(name)
    if client is None:
        raise HTTPException(400, f"unknown client {name}")
    question, options, rubric = req.resolved()
    with tracer.start_as_current_span(f"decision.{req.question_id}") as span:
        t0 = time.perf_counter()
        d = await client.decide(
            question,
            options,
            req.context,
            rubric,
            question_id=req.question_id,
            decision_type=req.decision_type,
        )
        router_ms = (time.perf_counter() - t0) * 1000
        d.escalated = (
            max(d.scores.values()) < cfg.escalation_threshold or d.rationale == CONTENT_FILTERED
        )
        span.set_attribute("driftline.decision_type", req.decision_type)
        span.set_attribute("driftline.incident_id", req.incident_id or "")
        span.set_attribute("driftline.caller", who.app_id or who.subject)
        span.set_attribute("driftline.router_latency_ms", round(router_ms, 1))
        span.set_attribute("gen_ai.request.model", d.model)
        for k, v in d.model_dump().items():
            span.set_attribute(
                f"decision.{k}", v if isinstance(v, (str, int, float, bool)) else str(v)
            )
        trace_id = format(span.get_span_context().trace_id, "032x")
    app.state.sink.put(row(d, incident_id=req.incident_id, trace_id=trace_id))
    return d
