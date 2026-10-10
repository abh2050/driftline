"""detector (spec 5.1.5): runs on a Dapr cron binding every minute.

For every stream with new windows in Cosmos it advances the EWMA/CUSUM state, asks the
router for D1 on a sustained breach, and when D1 says "real" stores and publishes an
Incident. Open incidents close when the metrics recover.
"""

from __future__ import annotations

import logging
import os
import statistics
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI
from opentelemetry import trace

from detector.cusum import (
    Breach,
    StreamState,
    advance,
    breach_windows,
    incident_baseline_hash,
    reset_after_close,
    suppress,
)
from driftline_common.auth import ServiceToken
from driftline_common.config import load_detector_config
from driftline_common.dapr import DaprPublisher
from driftline_common.schemas import Incident, MetricDelta, Window
from driftline_common.telemetry import inject_context, setup_telemetry

log = logging.getLogger("detector")
tracer = trace.get_tracer("detector")
ROUTER_URL = os.environ.get("ROUTER_URL", "http://router")
PATIENT_RAG_URL = os.environ.get("PATIENT_RAG_URL", "http://patient-rag")
MAX_WINDOWS_PER_TICK = 5000


def status_counts(run: list[Window]) -> dict[str, int]:
    out: dict[str, int] = {}
    for w in run:
        for code, n in w.status_codes.items():
            out[code] = out.get(code, 0) + n
    return out


def stage_latency(st: StreamState, run: list[Window]) -> dict[str, dict[str, float | None]]:
    """Median per-window stage p95 before the breach run and during it."""
    during_ids = {w.id for w in run}
    before = [w for w in st.recent if w.id not in during_ids]

    def med(ws: list[Window], attr: str) -> float | None:
        xs = [v for w in ws if (v := getattr(w, attr)) is not None]
        return round(statistics.median(xs), 1) if xs else None

    return {
        stage: {"before": med(before, attr), "during": med(run, attr)}
        for stage, attr in (("retrieval", "retrieval_p95_ms"), ("llm", "llm_p95_ms"))
    }


def config_diff(
    registry: dict[str, Any], baseline: str | None, current: str
) -> list[dict[str, object]]:
    if baseline is None or baseline == current:
        return []
    a = registry.get(baseline, {}).get("config")
    b = registry.get(current, {}).get("config")
    if not a or not b:
        return [{"key": "config_hash", "from": baseline, "to": current}]
    return [{"key": k, "from": a.get(k), "to": b.get(k)} for k in sorted(b) if a.get(k) != b.get(k)]


def summarize(breach: Breach, diff: list[dict[str, object]], windows: int) -> str:
    parts = [
        f"{m} {d['baseline']} -> {d['current']} ({d['delta_pct']:+.1f}%)"
        for m, d in breach.deltas.items()
    ]
    changes = "; ".join(f"{d['key']}: {d['from']} -> {d['to']}" for d in diff) or "none"
    return (
        f"Breach sustained for {windows} one-minute windows. Metrics: {', '.join(parts)}. "
        f"Config changes since baseline: {changes}."
    )


class Detector:
    def __init__(self) -> None:
        self.cfg = load_detector_config()
        self.cred = DefaultAzureCredential()
        self.cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=self.cred)
        db = self.cosmos.get_database_client("driftline")
        self.windows = db.get_container_client("windows")
        self.baselines = db.get_container_client("baselines")
        self.incidents = db.get_container_client("incidents")
        self.publisher = DaprPublisher(os.environ.get("PUBSUB_NAME", "pubsub-detector"))
        self.http = httpx.AsyncClient(timeout=60)
        self.token = ServiceToken(self.cred)
        self.registry: dict[str, Any] = {}

    async def close(self) -> None:
        await self.http.aclose()
        await self.publisher.close()
        await self.cosmos.close()
        await self.cred.close()

    async def load_registry(self) -> None:
        try:
            self.registry = (await self.http.get(f"{PATIENT_RAG_URL}/configs")).json()
        except httpx.HTTPError:
            log.warning("config registry unavailable; config diffs will use hashes only")

    async def streams(self) -> list[str]:
        q = "SELECT DISTINCT VALUE c.stream FROM c"
        return [str(s) async for s in self.windows.query_items(q)]

    async def load_state(self, stream: str) -> StreamState:
        try:
            doc = await self.baselines.read_item(f"baseline|{stream}", partition_key=stream)
            return StreamState.from_doc(doc)
        except Exception:
            return StreamState(stream)

    async def new_windows(self, st: StreamState) -> list[Window]:
        q = (
            "SELECT * FROM c WHERE c.stream = @s AND c.start > @after "
            "ORDER BY c.start OFFSET 0 LIMIT @limit"
        )
        params: list[dict[str, Any]] = [
            {"name": "@s", "value": st.stream},
            {"name": "@after", "value": st.last_window or ""},
            {"name": "@limit", "value": MAX_WINDOWS_PER_TICK},
        ]
        items = self.windows.query_items(q, parameters=params, partition_key=st.stream)
        return [
            Window.model_validate({k: v for k, v in it.items() if not k.startswith("_")})
            async for it in items
        ]

    async def ask_d1(self, st: StreamState, breach: Breach, run: list[Window]) -> dict[str, Any]:
        baseline = incident_baseline_hash(st, run[0].start, self.cfg)
        diff = config_diff(self.registry, baseline, run[-1].config_hash)
        commits = sorted({c for w in run for c in w.git_commits})
        context = {
            "summary": summarize(breach, diff, len(run)),
            "metric_deltas": breach.deltas,
            "window_count": len(run),
            "config_diff": diff,
            "recent_deploys": [{"commit": c} for c in commits if c not in st.commits_seen[:-5]],
        }
        resp = await self.http.post(
            f"{ROUTER_URL}/decide",
            json={
                "decision_type": "D1",
                "question_id": f"d1-{uuid.uuid4().hex[:8]}",
                "context": context,
            },
            headers={**await self.token.header(), **inject_context()},
        )
        resp.raise_for_status()
        return {"decision": resp.json(), "context": context, "diff": diff, "baseline": baseline}

    async def open_incident(
        self, st: StreamState, breach: Breach, run: list[Window], d1: dict[str, Any]
    ) -> Incident:
        incident_id = f"inc-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        with tracer.start_as_current_span("detector.incident") as span:
            span.set_attribute("driftline.incident_id", incident_id)
            span.set_attribute("driftline.stream", st.stream)
            carrier = inject_context()
            incident = Incident(
                id=incident_id,
                stream=st.stream,
                opened_at=run[0].start,
                detected_at=datetime.now(UTC),
                breached_metrics=breach.metrics,
                metric_deltas={m: MetricDelta(**d) for m, d in breach.deltas.items()},
                window_count=len(run),
                config_hash=run[-1].config_hash,
                baseline_config_hash=d1["baseline"] or run[0].config_hash,
                config_diff=d1["diff"],
                recent_deploys=d1["context"]["recent_deploys"],
                error_status_counts=status_counts(run),
                stage_latency_p95_ms=stage_latency(st, run),
                d1=d1["decision"],
                fault_label=",".join(sorted({f for w in run for f in w.fault_ids})) or None,
                traceparent=carrier.get("traceparent"),
            )
            await self.incidents.upsert_item(incident.model_dump(mode="json"))
            payload = {"event": "opened", "incident": incident.model_dump(mode="json")}
            await self.publisher.publish("incidents", payload)
        return incident

    async def close_incident(self, incident_id: str, at: str) -> None:
        try:
            doc = await self.incidents.read_item(incident_id, partition_key="incident")
        except Exception:
            return
        if doc.get("status") not in ("resolved", "closed"):
            doc["status"] = "closed"
            doc["closed_at"] = at
            await self.incidents.upsert_item(doc)
            await self.publisher.publish("incidents", {"event": "closed", "incident": doc})

    async def process(self, stream: str) -> dict[str, int]:
        st = await self.load_state(stream)
        stats = {"windows": 0, "breaches": 0, "incidents": 0, "closed": 0}
        for w in await self.new_windows(st):
            stats["windows"] += 1
            action, breach = advance(st, w, self.cfg)
            if action == "breach" and breach is not None:
                stats["breaches"] += 1
                run = breach_windows(st, breach, self.cfg)
                with tracer.start_as_current_span("detector.verify"):
                    d1 = await self.ask_d1(st, breach, run)
                if d1["decision"]["chosen"] == "real":
                    st.open_incident = (await self.open_incident(st, breach, run, d1)).id
                    stats["incidents"] += 1
                else:
                    suppress(st, breach.metrics, self.cfg)
            elif action == "recovered" and st.open_incident:
                await self.close_incident(st.open_incident, w.start.isoformat())
                reset_after_close(st)
                stats["closed"] += 1
        await self.baselines.upsert_item(st.to_doc())
        return stats


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tel = setup_telemetry("detector")
    app.state.detector = Detector()
    try:
        yield
    finally:
        await app.state.detector.close()
        tel.shutdown()


app = FastAPI(title="detector", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/detector-cron")
async def tick() -> dict[str, Any]:
    """Dapr cron input binding (component `detector-cron`)."""
    det: Detector = app.state.detector
    with tracer.start_as_current_span("detector.tick"):
        await det.load_registry()
        results = {s: await det.process(s) for s in await det.streams()}
    if any(r["windows"] for r in results.values()):
        log.info("tick %s", results)
    return results
