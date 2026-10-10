"""Offline detector simulation over synthetic telemetry (used by tests and layer 2b).

Feeds events through the same window aggregation and CUSUM code the services run.
D1 is replaced by a callback so the detection layer can be measured on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from detector.cusum import (
    Breach,
    StreamState,
    advance,
    breach_windows,
    reset_after_close,
    suppress,
)
from driftline_common.config import DetectorConfig
from driftline_common.schemas import TelemetryEvent, Window
from ingest.windows import WindowAggregator


@dataclass
class SimIncident:
    opened_at: str
    detected_window: str
    metrics: list[str]
    fault_ids: list[str]
    closed_at: str | None = None


def windows_from_events(events: Iterable[TelemetryEvent]) -> list[Window]:
    agg = WindowAggregator()
    out: list[Window] = []
    for e in events:
        agg.add(e)
        out.extend(agg.flush())
    out.extend(agg.flush(force=True))
    return out


def simulate(
    windows: list[Window],
    cfg: DetectorConfig,
    verify: Callable[[Breach, list[Window]], bool] = lambda b, w: True,
    state: StreamState | None = None,
) -> tuple[list[SimIncident], StreamState]:
    st = state or StreamState(windows[0].stream if windows else "sim")
    incidents: list[SimIncident] = []
    for w in windows:
        action, breach = advance(st, w, cfg)
        if action == "breach" and breach is not None:
            run = breach_windows(st, breach, cfg)
            if verify(breach, run):
                st.open_incident = f"sim-{len(incidents) + 1}"
                incidents.append(
                    SimIncident(
                        run[0].start.isoformat(),
                        w.start.isoformat(),
                        breach.metrics,
                        sorted({f for x in run for f in x.fault_ids}),
                    )
                )
            else:
                suppress(st, breach.metrics, cfg)
        elif action == "recovered":
            incidents[-1].closed_at = w.start.isoformat()
            reset_after_close(st)
    return incidents, st
