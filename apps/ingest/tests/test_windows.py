from __future__ import annotations

from datetime import UTC, datetime, timedelta

from driftline_common.schemas import TelemetryEvent
from ingest.windows import WindowAggregator


def ev(ts: datetime, **kw: object) -> TelemetryEvent:
    base: dict[str, object] = dict(
        event_id=f"e{ts.timestamp()}",
        ts=ts,
        persona="synthetic",
        latency_ms=1000,
        retrieval_latency_ms=100,
        llm_latency_ms=900,
        top_score=0.8,
        mean_score=0.7,
        hit_count=8,
        citation_count=2,
        tokens_in=1000,
        tokens_out=100,
        cost_usd=0.001,
        config_hash="abc",
        prompt_version="v3",
        git_commit="c1",
        stream="s1",
    )
    base.update(kw)
    return TelemetryEvent.model_validate(base)


def test_windows_close_by_event_time_watermark() -> None:
    agg = WindowAggregator(grace=timedelta(seconds=30))
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    for i in range(6):
        agg.add(ev(t0 + timedelta(seconds=10 * i)))
    assert agg.flush() == []  # minute not closed yet
    agg.add(ev(t0 + timedelta(minutes=1, seconds=40)))
    (w,) = agg.flush()
    assert w.count == 6
    assert w.start == t0
    assert w.id == f"s1|{t0.isoformat()}"


def test_window_metrics() -> None:
    agg = WindowAggregator()
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    agg.add(ev(t0, groundedness=4.0, canary_correct=True))
    agg.add(ev(t0 + timedelta(seconds=10), citation_count=0, canary_correct=False))
    agg.add(ev(t0 + timedelta(seconds=20), error=True, status_code=429, top_score=None))
    (w,) = agg.flush(force=True)
    assert w.count == 3
    assert w.errors == 1
    assert w.citation_rate == 0.5
    assert w.groundedness == 4.0
    assert w.canary_accuracy == 0.5
    assert w.error_rate == 1 / 3
    assert w.status_codes == {"429": 1}
    assert w.retrieval_p95_ms == 100
    assert w.llm_p95_ms == 900
