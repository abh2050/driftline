"""Per-stream change detection: EWMA baselines and one-sided CUSUM per metric (spec 5.1.5).

Pure and serializable, so the detector service can persist state in Cosmos `baselines`
between ticks and the same code runs in tests and in layer 2b offline replays.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from driftline_common.config import DetectorConfig
from driftline_common.schemas import METRICS, WORSE_WHEN, Window


@dataclass
class MetricState:
    mean: float | None = None
    var: float = 0.0
    n: int = 0
    s: float = 0.0  # one-sided CUSUM in the "worse" direction
    cooldown: int = 0


@dataclass
class Observation:
    """Pooled metrics over the last `aggregate_windows` windows."""

    end: datetime
    values: dict[str, float | None]
    count: int
    config_hash: str
    git_commits: list[str]
    fault_ids: list[str]


@dataclass
class Breach:
    metrics: list[str]
    deltas: dict[str, dict[str, float]]
    run: int


@dataclass
class StreamState:
    stream: str
    metrics: dict[str, MetricState] = field(
        default_factory=lambda: {m: MetricState() for m in METRICS}
    )
    observations: int = 0
    breach_run: int = 0
    healthy_run: int = 0
    open_incident: str | None = None
    baseline_config_hash: str | None = None
    last_window: str | None = None
    pending: int = 0  # windows received since the last evaluated block
    recent: deque[Window] = field(default_factory=lambda: deque(maxlen=30))
    commits_seen: list[str] = field(default_factory=list)
    # (window start, config hash) each time the running config changes (ADR 0007)
    config_log: list[tuple[str, str]] = field(default_factory=list)

    def to_doc(self) -> dict[str, Any]:
        return {
            "id": f"baseline|{self.stream}",
            "pk": self.stream,
            "stream": self.stream,
            "metrics": {k: vars(v) for k, v in self.metrics.items()},
            "observations": self.observations,
            "breach_run": self.breach_run,
            "healthy_run": self.healthy_run,
            "open_incident": self.open_incident,
            "baseline_config_hash": self.baseline_config_hash,
            "last_window": self.last_window,
            "pending": self.pending,
            "recent": [w.model_dump(mode="json") for w in self.recent],
            "commits_seen": self.commits_seen[-20:],
            "config_log": [list(e) for e in self.config_log[-20:]],
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> StreamState:
        st = cls(doc["stream"])
        st.metrics = {k: MetricState(**v) for k, v in doc["metrics"].items()}
        for m in METRICS:
            st.metrics.setdefault(m, MetricState())
        st.observations = doc["observations"]
        st.breach_run = doc["breach_run"]
        st.healthy_run = doc["healthy_run"]
        st.open_incident = doc["open_incident"]
        st.baseline_config_hash = doc["baseline_config_hash"]
        st.last_window = doc["last_window"]
        st.pending = int(doc.get("pending", 0))
        st.recent = deque((Window.model_validate(w) for w in doc["recent"]), maxlen=30)
        st.commits_seen = list(doc.get("commits_seen", []))
        st.config_log = [(str(a), str(h)) for a, h in doc.get("config_log", [])]
        return st


def incident_baseline_hash(st: StreamState, run_start: datetime, cfg: DetectorConfig) -> str | None:
    """The config in effect `config_lookback_minutes` before the breach run began.

    A gradual fault keeps blocks below the CUSUM threshold for a while, so "the config of the
    last healthy block" can already be the faulted one; the change log is not fooled by that.
    """
    if not st.config_log:
        return st.baseline_config_hash
    cutoff = (run_start - timedelta(minutes=cfg.config_lookback_minutes)).isoformat()
    before = [h for at, h in st.config_log if at <= cutoff]
    return before[-1] if before else st.config_log[0][1]


def pool(windows: list[Window], cfg: DetectorConfig) -> Observation:
    """Count-weighted pooling; sparse metrics (groundedness, canary) pool their samples."""
    total = sum(w.count for w in windows)
    values: dict[str, float | None] = {}
    for m in METRICS:
        if m == "groundedness":
            n = sum(w.groundedness_n for w in windows)
            values[m] = (
                sum((w.groundedness or 0) * w.groundedness_n for w in windows) / n
                if n >= cfg.metrics[m].min_samples
                else None
            )
        elif m == "canary_accuracy":
            n = sum(w.canary_n for w in windows)
            values[m] = (
                sum(w.canary_correct for w in windows) / n
                if n >= cfg.metrics[m].min_samples
                else None
            )
        elif m == "error_rate":
            values[m] = sum(w.errors for w in windows) / total if total else None
        elif m == "latency_p95_ms":
            values[m] = statistics.median(w.latency_p95_ms for w in windows) if windows else None
        else:
            pts: list[tuple[float, int]] = []
            for w in windows:
                v, c = w.metric(m), w.count - w.errors
                if v is not None and c > 0:
                    pts.append((v, c))
            weight = sum(c for _, c in pts)
            values[m] = sum(v * c for v, c in pts) / weight if weight else None
    last = windows[-1]
    return Observation(
        end=last.start,
        values=values,
        count=total,
        config_hash=last.config_hash,
        git_commits=sorted({c for w in windows for c in w.git_commits}),
        fault_ids=sorted({f for w in windows for f in w.fault_ids}),
    )


def step(st: StreamState, obs: Observation, cfg: DetectorConfig) -> Breach | None:
    """Advance one observation. Returns a Breach when the breach has been sustained."""
    st.observations += 1
    warm = st.observations > cfg.warmup_windows
    breached: list[str] = []
    deltas: dict[str, dict[str, float]] = {}
    for m, ms in st.metrics.items():
        x = obs.values.get(m)
        rule = cfg.metrics.get(m)
        if x is None or rule is None:
            continue
        if ms.mean is None:
            ms.mean, ms.var, ms.n = x, 0.0, 1
            continue
        sd = max(math.sqrt(ms.var), rule.sd_floor)
        worse = (ms.mean - x) if WORSE_WHEN[m] == "down" else (x - ms.mean)
        z = worse / sd
        ms.s = max(0.0, ms.s + z - cfg.cusum_k) if warm else 0.0
        change = abs(x - ms.mean)
        big_enough = change >= max(rule.min_abs_change, rule.min_rel_change * abs(ms.mean))
        if ms.cooldown > 0:
            ms.cooldown -= 1
            ms.s = 0.0
        alarm = warm and ms.s > cfg.cusum_h and worse > 0 and big_enough
        if alarm:
            breached.append(m)
            deltas[m] = {
                "baseline": round(ms.mean, 4),
                "current": round(x, 4),
                "delta_pct": round((x - ms.mean) / abs(ms.mean) * 100, 1) if ms.mean else 0.0,
                "cusum": round(ms.s, 2),
            }
        # Freeze the baseline while a metric is drifting or an incident is open.
        if ms.s < cfg.cusum_h / 2 and st.open_incident is None:
            alpha = rule.ewma_alpha or cfg.ewma_alpha
            a = alpha if ms.n > 20 else 1.0 / (ms.n + 1)
            diff = x - ms.mean
            ms.mean += a * diff
            ms.var = (1 - a) * (ms.var + a * diff * diff)
            ms.n += 1
    if st.baseline_config_hash is None or (
        not breached and st.open_incident is None and st.breach_run == 0
    ):
        st.baseline_config_hash = obs.config_hash
    for c in obs.git_commits:
        if c not in st.commits_seen:
            st.commits_seen.append(c)
    if breached:
        st.breach_run += 1
        st.healthy_run = 0
    else:
        st.breach_run = 0
        st.healthy_run += 1
    if breached and st.breach_run >= cfg.min_breach_windows and st.open_incident is None:
        return Breach(breached, deltas, st.breach_run)
    return None


def suppress(st: StreamState, metrics: list[str], cfg: DetectorConfig) -> None:
    """D1 said noise: reset those metrics' CUSUM and cool them down."""
    for m in metrics:
        st.metrics[m].s = 0.0
        st.metrics[m].cooldown = cfg.noise_cooldown_windows
    st.breach_run = 0


def recovered(st: StreamState, cfg: DetectorConfig) -> bool:
    return st.open_incident is not None and st.healthy_run >= cfg.recovery_windows


def reset_after_close(st: StreamState) -> None:
    st.open_incident = None
    for ms in st.metrics.values():
        ms.s = 0.0


def advance(st: StreamState, w: Window, cfg: DetectorConfig) -> tuple[str, Breach | None]:
    """Feed one window. Returns ("breach", b) when D1 should be asked, ("recovered", None)
    when an open incident's metrics have recovered, else ("none", None)."""
    st.last_window = w.start.isoformat()
    if not st.config_log or st.config_log[-1][1] != w.config_hash:
        st.config_log = [*st.config_log[-19:], (w.start.isoformat(), w.config_hash)]
    if w.count < cfg.min_window_count:
        return "none", None
    st.recent.append(w)
    st.pending += 1
    # Evaluate on non-overlapping blocks: overlapping pools are autocorrelated and would make
    # CUSUM accumulate several times faster than its thresholds assume.
    if st.pending < cfg.aggregate_windows:
        return "none", None
    st.pending = 0
    breach = step(st, pool(list(st.recent)[-cfg.aggregate_windows :], cfg), cfg)
    if breach is not None:
        return "breach", breach
    if recovered(st, cfg):
        return "recovered", None
    return "none", None


def breach_windows(st: StreamState, breach: Breach, cfg: DetectorConfig) -> list[Window]:
    return list(st.recent)[-breach.run * cfg.aggregate_windows :]
