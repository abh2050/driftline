"""Event-time one-minute window aggregation (spec 5.1.4).

Windows close by watermark (latest event time seen minus a grace period), so accelerated
replays aggregate exactly like live traffic.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from driftline_common.schemas import TelemetryEvent, Window


def minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


@dataclass
class _Acc:
    events: list[TelemetryEvent] = field(default_factory=list)


def build_window(stream: str, start: datetime, events: list[TelemetryEvent]) -> Window:
    ok = [e for e in events if not e.error]
    lat = sorted(e.latency_ms for e in events)

    def mean(xs: list[float]) -> float | None:
        return round(statistics.fmean(xs), 4) if xs else None

    grounded = [e.groundedness for e in ok if e.groundedness is not None]
    canary = [e.canary_correct for e in events if e.canary_correct is not None]
    # the dominant config in the minute (a fault flips it mid-window)
    config = statistics.mode(e.config_hash for e in events)
    prompt = statistics.mode(e.prompt_version for e in events)
    return Window(
        id=f"{stream}|{start.isoformat()}",
        pk=stream,
        stream=stream,
        start=start,
        config_hash=config,
        prompt_version=prompt,
        git_commits=sorted({e.git_commit for e in events}),
        fault_ids=sorted({e.fault_id for e in events if e.fault_id}),
        count=len(events),
        errors=len(events) - len(ok),
        latency_p50_ms=round(lat[len(lat) // 2], 1),
        latency_p95_ms=round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
        top_score=mean([e.top_score for e in ok if e.top_score is not None]),
        mean_score=mean([e.mean_score for e in ok if e.mean_score is not None]),
        hit_count=mean([float(e.hit_count) for e in ok]),
        citation_rate=mean([1.0 if e.citation_count > 0 else 0.0 for e in ok]),
        groundedness=mean(grounded),
        groundedness_n=len(grounded),
        canary_n=len(canary),
        canary_correct=sum(1 for c in canary if c),
        tokens_in=sum(e.tokens_in for e in events),
        tokens_out=sum(e.tokens_out for e in events),
        cost_usd=round(sum(e.cost_usd for e in events), 6),
    )


class WindowAggregator:
    def __init__(self, grace: timedelta = timedelta(seconds=90)) -> None:
        self.grace = grace
        self._open: dict[tuple[str, datetime], _Acc] = defaultdict(_Acc)
        self._watermark: dict[str, datetime] = {}

    def add(self, e: TelemetryEvent) -> None:
        self._open[(e.stream, minute(e.ts))].events.append(e)
        wm = self._watermark.get(e.stream)
        if wm is None or e.ts > wm:
            self._watermark[e.stream] = e.ts

    def flush(self, *, force: bool = False) -> list[Window]:
        """Close windows whose minute ended before the stream's watermark minus grace."""
        out = []
        for key in sorted(self._open, key=lambda k: (k[0], k[1])):
            stream, start = key
            wm = self._watermark.get(stream)
            if force or (wm is not None and start + timedelta(minutes=1) + self.grace <= wm):
                acc = self._open.pop(key)
                out.append(build_window(stream, start, acc.events))
        return out

    def pending(self) -> int:
        return sum(len(a.events) for a in self._open.values())
