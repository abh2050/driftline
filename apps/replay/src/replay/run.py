"""replay (spec 5.1.3): push synthetic telemetry onto the `telemetry` topic at a chosen speed.

Each run publishes into its own stream ("replay-<run id>") so replays never mix with live
traffic or each other. Events keep their synthetic timestamps; ingest and the detector work
in event time, so a 1440x replay behaves like a day of real traffic in a minute.

Plans (REPLAY_PLAN):
  clean:<hours>          the last <hours> of the clean baseline (false-positive runs)
  fault:<F#>:<trial>     6 hours of baseline warm-up, then the fault trial segment
  days:<n>               the first <n> baseline days
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient
from opentelemetry import trace

from driftline_common.telemetry import inject_context, setup_telemetry

DATA = Path(
    os.environ.get("TELEMETRY_DIR", Path(__file__).resolve().parents[4] / "data" / "telemetry")
)
tracer = trace.get_tracer("replay")
WARMUP = timedelta(hours=6)


def _read(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _baseline_tail(duration: timedelta) -> list[dict[str, Any]]:
    days = sorted((DATA / "baseline").glob("day-*.jsonl.gz"))
    out: list[dict[str, Any]] = []
    for path in reversed(days):
        out = list(_read(path)) + out
        if len(out) * 10 >= duration.total_seconds():
            break
    keep = int(duration.total_seconds() // 10)
    return out[-keep:]


def plan_events(plan: str) -> list[dict[str, Any]]:
    kind, *args = plan.split(":")
    if kind == "clean":
        return _baseline_tail(timedelta(hours=float(args[0])))
    if kind == "fault":
        fault, trial = args[0], int(args[1]) if len(args) > 1 else 1
        return _baseline_tail(WARMUP) + list(_read(DATA / "faults" / f"{fault}-t{trial}.jsonl.gz"))
    if kind == "days":
        days = sorted((DATA / "baseline").glob("day-*.jsonl.gz"))[: int(args[0])]
        return [e for p in days for e in _read(p)]
    raise ValueError(f"unknown replay plan {plan!r}")


def cloud_event(event: dict[str, Any], stream: str, traceparent: str | None) -> dict[str, Any]:
    ce: dict[str, Any] = {
        "specversion": "1.0",
        "type": "com.dapr.event.sent",
        "source": "replay",
        "id": str(uuid.uuid4()),
        "datacontenttype": "application/json",
        "topic": "telemetry",
        "pubsubname": "pubsub-ingest",
        "data": {**event, "stream": stream},
    }
    if traceparent:
        ce["traceparent"] = traceparent
    return ce


async def publish(events: list[dict[str, Any]], stream: str, speed: float) -> dict[str, Any]:
    fqdn = os.environ["SERVICEBUS_FQDN"]
    rate = speed / 10.0  # synthetic traffic is one event per 10 seconds
    batch_size = max(1, min(500, int(rate)))
    cred = DefaultAzureCredential()
    sent = 0
    t0 = time.monotonic()
    with tracer.start_as_current_span("replay.run") as span:
        span.set_attribute("driftline.stream", stream)
        span.set_attribute("driftline.events", len(events))
        traceparent = inject_context().get("traceparent")
        async with (
            ServiceBusClient(fqdn, cred) as client,
            client.get_topic_sender("telemetry") as sender,
        ):
            for start in range(0, len(events), batch_size):
                chunk = events[start : start + batch_size]
                batch = await sender.create_message_batch()
                for e in chunk:
                    msg = ServiceBusMessage(
                        json.dumps(cloud_event(e, stream, traceparent)),
                        content_type="application/cloudevents+json",
                    )
                    try:
                        batch.add_message(msg)
                    except ValueError:  # batch full
                        await sender.send_messages(batch)
                        batch = await sender.create_message_batch()
                        batch.add_message(msg)
                await sender.send_messages(batch)
                sent += len(chunk)
                # pace to the requested speed
                ahead = sent / rate - (time.monotonic() - t0)
                if ahead > 0:
                    await asyncio.sleep(ahead)
    await cred.close()
    return {"stream": stream, "events": sent, "seconds": round(time.monotonic() - t0, 1)}


def main() -> None:
    tel = setup_telemetry("replay")
    plan = os.environ.get("REPLAY_PLAN", "clean:48")
    speed = float(os.environ.get("REPLAY_SPEED", "1440"))
    run_id = os.environ.get("REPLAY_RUN_ID") or uuid.uuid4().hex[:8]
    stream = f"replay-{run_id}"
    try:
        events = plan_events(plan)
        print(json.dumps({"plan": plan, **asyncio.run(publish(events, stream, speed))}))
    finally:
        tel.shutdown()


if __name__ == "__main__":
    main()
