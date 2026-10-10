"""One traffic-gen run: seasonal query volume plus canary questions (spec 5.1.2).

Volume follows evals/datasets/seasonality.json for the current UTC hour and weekday.
Canaries are golden questions whose expected value depends on the source version that
patient-rag reports, so a stale index (F5) shows up as canary_correct=false.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from opentelemetry import trace

from driftline_common.auth import ServiceToken
from driftline_common.config import config_dir
from driftline_common.telemetry import setup_telemetry

DATASETS = Path(
    os.environ.get("DATASETS_DIR", Path(__file__).resolve().parents[4] / "evals" / "datasets")
)
tracer = trace.get_tracer("traffic-gen")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def plan_run(
    now: datetime,
    cfg: dict[str, Any],
    traffic: list[dict[str, Any]],
    golden: list[dict[str, Any]],
    season: dict[str, Any],
    source_version: int,
) -> list[dict[str, Any]]:
    """Deterministic for a given run time, so a replayed run sends the same requests."""
    r = random.Random(int(now.timestamp()) // 300)  # noqa: S311 - not security sensitive
    hour, weekday = now.hour, now.weekday()
    mix = {
        p: season["persona_share"][p] * season["hourly"][p][hour] for p in season["persona_share"]
    }
    peak = max(
        sum(season["persona_share"][p] * season["hourly"][p][h] for p in season["persona_share"])
        for h in range(24)
    )
    load = sum(mix.values()) / peak * season["weekday"][weekday]
    n = max(1, round(cfg["queries_per_run_at_peak"] * load))
    by_persona: dict[str, list[dict[str, Any]]] = {}
    for q in traffic:
        by_persona.setdefault(q["persona"], []).append(q)
    personas = r.choices(list(mix), weights=list(mix.values()), k=n)
    reqs = [
        {"query": (q := r.choice(by_persona[p]))["query"], "persona": p, "query_id": q["id"]}
        for p in personas
    ]
    for g in r.sample(golden, cfg["canaries_per_run"]):
        expected = g["answer_value"]
        if g["kind"] == "fresh" and source_version < 2:
            expected = g["stale_value"]
        reqs.append(
            {
                "query": g["question"],
                "persona": "synthetic",
                "query_id": g["id"],
                "expected": [expected],
            }
        )
    return reqs


async def run() -> dict[str, int]:
    cfg = yaml.safe_load((config_dir() / "traffic.yaml").read_text())
    base = os.environ.get("PATIENT_RAG_URL", cfg["patient_rag_url"]).rstrip("/")
    traffic = load_jsonl(DATASETS / "traffic.jsonl")
    golden = [g for g in load_jsonl(DATASETS / "golden.jsonl") if not g.get("rejected")]
    season = json.loads((DATASETS / "seasonality.json").read_text())
    token = ServiceToken()
    stats = {"sent": 0, "ok": 0, "errors": 0}
    async with httpx.AsyncClient(timeout=120) as http:
        with tracer.start_as_current_span("traffic-gen.run") as span:
            state = (await http.get(f"{base}/state")).json()
            reqs = plan_run(
                datetime.now(UTC), cfg, traffic, golden, season, int(state.get("source_version", 1))
            )
            span.set_attribute("driftline.requests", len(reqs))
            sem = asyncio.Semaphore(cfg["concurrency"])

            async def send(body: dict[str, Any]) -> None:
                async with sem:
                    stats["sent"] += 1
                    try:
                        resp = await http.post(
                            f"{base}/ask", json=body, headers=await token.header()
                        )
                        stats["ok" if resp.status_code == 200 else "errors"] += 1
                    except httpx.HTTPError:
                        stats["errors"] += 1

            await asyncio.gather(*(send(b) for b in reqs))
    await token.close()
    return stats


def main() -> None:
    tel = setup_telemetry("traffic-gen")
    try:
        print(json.dumps(asyncio.run(run())))
    finally:
        tel.shutdown()


if __name__ == "__main__":
    main()
