"""sandbox-runner (spec 5.1.8): prove a candidate fix with layer 1 before a PR exists.

Triggered by a message on the `sandbox` topic (Container Apps job, KEDA Service Bus rule).
For the candidate config (current config + the specialist's changes) it builds patient-rag in
process, runs a fixed layer 1 subset of the golden set, compares against the baseline
fixture on the same questions, applies evals/gates.yaml, stores the results in the
`eval-results` container and publishes a summary to the `validations` topic.

If the change touches indexing parameters, it builds a temporary index `sbx-<run id>` (the
only indexes this identity may create, enforced here) and deletes it afterwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient
from azure.storage.blob.aio import BlobServiceClient
from opentelemetry import context, trace

from driftline_common.telemetry import extract_context, inject_context, setup_telemetry
from evals.layer1.metrics import JUDGE_METRICS, aggregate, gate, paired_stderr
from evals.layer1.run import FIXTURES, answer_all, judge, load_golden
from patient_rag.clients import Clients
from patient_rag.config import apply, load_base_config
from patient_rag.index import build_index, delete_index

log = logging.getLogger("sandbox-runner")
tracer = trace.get_tracer("sandbox-runner")
EVALS = Path(os.environ.get("EVALS_DIR", Path(__file__).resolve().parents[4] / "evals"))
INDEX_KEYS = {"chunk_size", "chunk_overlap", "embedding_deployment", "embedding_dimensions"}
SUBSET = int(os.environ.get("SANDBOX_SUBSET", "40"))
# incident metric -> the layer 1 metric that must recover
METRIC_MAP = {
    "groundedness": "groundedness",
    "top_score": "recall_at_k",
    "hit_count": "recall_at_k",
    "citation_rate": "citation_rate",
    "canary_accuracy": "answer_accuracy",
    "latency_p95_ms": "latency_p95_ms",
    "error_rate": "answer_accuracy",
}


def subset(golden: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Deterministic stratified sample (standard and fresh questions)."""
    r = random.Random(20261010)  # noqa: S311 - reproducible sample, not security
    std = [g for g in golden if g["kind"] == "standard"]
    fresh = [g for g in golden if g["kind"] == "fresh"]
    k_fresh = max(1, n // 4)
    return sorted(r.sample(std, n - k_fresh) + r.sample(fresh, k_fresh), key=lambda g: g["id"])


def baseline_rows(ids: set[str]) -> list[dict[str, Any]]:
    rows = [
        json.loads(x) for x in (FIXTURES / "baseline.jsonl").read_text().splitlines() if x.strip()
    ]
    return [r for r in rows if r["id"] in ids]


def target_metric(incident_metrics: list[str]) -> str:
    for m in incident_metrics:
        if m in METRIC_MAP:
            return METRIC_MAP[m]
    return "groundedness"


async def validate(request: dict[str, Any]) -> dict[str, Any]:
    run_id = request.get("run_id") or uuid.uuid4().hex[:8]
    changes: dict[str, Any] = request["changes"]
    # start from the config patient-rag was running when the incident opened, so a change
    # that does not address the cause fails the gate
    running: dict[str, Any] = request.get("running", {})
    overrides = {**running, **changes}
    base = load_base_config()
    cfg = apply(base, overrides)
    golden = subset(load_golden(None), SUBSET)
    ids = {g["id"] for g in golden}
    index_name = cfg.index_name()
    temp_index = None
    clients = Clients(max_retries=8)
    try:
        if INDEX_KEYS & set(overrides) and cfg.index_name() != base.index_name():
            temp_index = f"sbx-{run_id}"
            if not temp_index.startswith("sbx-"):
                raise RuntimeError("sandbox may only create sbx-* indexes")
            with tracer.start_as_current_span("sandbox-runner.build_index"):
                await build_index(clients, cfg, name=temp_index)
            index_name = temp_index
        with tracer.start_as_current_span("sandbox-runner.layer1") as span:
            span.set_attribute("driftline.incident_id", request["incident_id"])
            rows, _ = await answer_all(
                golden, overrides, source_version=1, concurrency=2, index_name=index_name
            )
            await asyncio.to_thread(judge, rows, label=f"sandbox-{run_id}", upload=False)
    finally:
        if temp_index:
            try:
                await delete_index(clients, temp_index)
            except Exception:
                log.exception("could not delete %s", temp_index)
        await clients.close()
    candidate = aggregate(rows)
    base_rows = baseline_rows(ids)
    baseline = aggregate(base_rows)
    gates = yaml.safe_load((EVALS / "gates.yaml").read_text())
    metric = target_metric(request.get("breached_metrics", []))
    noise = {m: paired_stderr(base_rows, rows, m) for m in JUDGE_METRICS}
    result = gate(baseline, candidate, metric, gates, noise)
    return {
        "run_id": run_id,
        "incident_id": request["incident_id"],
        "changes": changes,
        "running": running,
        "config_hash": cfg.config_hash(),
        "questions": len(rows),
        "temp_index": temp_index,
        "target_metric": metric,
        "baseline": baseline,
        "candidate": candidate,
        "gate": {
            "passed": result.passed,
            "recovered": result.recovered,
            "regressions": result.regressions,
            "details": result.details,
        },
        "finished_at": datetime.now(UTC).isoformat(),
        "rows": rows,
    }


async def store(result: dict[str, Any], cred: DefaultAzureCredential) -> str:
    name = f"{result['incident_id']}/{result['run_id']}.json"
    async with BlobServiceClient(os.environ["STORAGE_BLOB_ENDPOINT"], credential=cred) as svc:
        await svc.get_container_client("eval-results").upload_blob(
            name, json.dumps(result, default=str, indent=1), overwrite=True
        )
    return name


def cloud_event(data: dict[str, Any], topic: str) -> str:
    return json.dumps(
        {
            "specversion": "1.0",
            "type": "com.dapr.event.sent",
            "source": "sandbox-runner",
            "id": str(uuid.uuid4()),
            "datacontenttype": "application/json",
            "topic": topic,
            "pubsubname": "pubsub-orchestrator",
            "data": data,
            **{k: v for k, v in inject_context().items()},
        }
    )


async def run_once() -> int:
    cred = DefaultAzureCredential()
    fqdn = os.environ["SERVICEBUS_FQDN"]
    handled = 0
    # A validation outlasts the 5-minute message lock; without renewal the completion fails
    # and the request is delivered again.
    renewer = AutoLockRenewer(max_lock_renewal_duration=3000)
    async with ServiceBusClient(fqdn, cred) as sb, renewer:
        receiver = sb.get_subscription_receiver("sandbox", "sandbox-runner", max_wait_time=30)
        async with receiver:
            for msg in await receiver.receive_messages(max_message_count=1, max_wait_time=30):
                renewer.register(receiver, msg)
                envelope = json.loads(b"".join(msg.body))
                request = envelope.get("data", envelope)
                token = context.attach(extract_context(envelope))
                try:
                    with tracer.start_as_current_span("sandbox-runner.validate") as span:
                        span.set_attribute("driftline.incident_id", request["incident_id"])
                        try:
                            result = await validate(request)
                            result["blob"] = await store(result, cred)
                        except Exception as e:
                            log.exception("validation failed")
                            result = {
                                "run_id": request.get("run_id"),
                                "incident_id": request["incident_id"],
                                "error": f"{type(e).__name__}: {e}"[:1000],
                                "gate": {"passed": False},
                            }
                        summary = {k: v for k, v in result.items() if k != "rows"}
                        async with sb.get_topic_sender("validations") as sender:
                            await sender.send_messages(
                                ServiceBusMessage(
                                    cloud_event(summary, "validations"),
                                    content_type="application/cloudevents+json",
                                )
                            )
                    await receiver.complete_message(msg)
                    handled += 1
                finally:
                    context.detach(token)
    await cred.close()
    return handled


def main() -> None:
    tel = setup_telemetry("sandbox-runner")
    try:
        print(json.dumps({"handled": asyncio.run(run_once())}))
    finally:
        tel.shutdown()


if __name__ == "__main__":
    main()
