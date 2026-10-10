"""Shared message schemas (Service Bus payloads and stored documents)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Persona = Literal[
    "maintenance_tech",
    "quality_engineer",
    "safety_officer",
    "plant_manager",
    "new_hire",
    "auditor",
    "synthetic",
]


class TelemetryEvent(BaseModel):
    """One patient-rag request, published to the `telemetry` topic."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    ts: datetime
    service: Literal["patient-rag"] = "patient-rag"
    persona: Persona
    query_id: str | None = None
    latency_ms: float = Field(ge=0)
    retrieval_latency_ms: float = Field(ge=0)
    llm_latency_ms: float = Field(ge=0)
    top_score: float | None = None
    mean_score: float | None = None
    hit_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    groundedness: float | None = Field(default=None, ge=1, le=5)
    error: bool = False
    status_code: int = 200
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    config_hash: str
    prompt_version: str
    git_commit: str
    # "live" for patient-rag traffic; replay runs use "replay-<run id>" so they never mix.
    stream: str = "live"
    # Canary requests carry an expected answer; True when the answer contained it.
    canary_correct: bool | None = None
    # Synthetic replay only: the true fault label of the segment, None when clean.
    fault_id: str | None = None


METRICS = (
    "groundedness",
    "top_score",
    "citation_rate",
    "canary_accuracy",
    "hit_count",
    "latency_p95_ms",
    "error_rate",
)
# Direction in which a metric gets worse.
WORSE_WHEN: dict[str, Literal["down", "up"]] = {
    "groundedness": "down",
    "top_score": "down",
    "citation_rate": "down",
    "canary_accuracy": "down",
    "hit_count": "down",
    "latency_p95_ms": "up",
    "error_rate": "up",
}


class Window(BaseModel):
    """One-minute aggregate written by ingest to Cosmos `windows` (pk = stream)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    pk: str  # stream
    stream: str
    start: datetime
    config_hash: str
    prompt_version: str
    git_commits: list[str]
    fault_ids: list[str] = Field(default_factory=list)
    count: int
    errors: int
    latency_p50_ms: float
    latency_p95_ms: float
    status_codes: dict[str, int] = Field(default_factory=dict)  # non-200 responses
    retrieval_p95_ms: float | None = None
    llm_p95_ms: float | None = None
    top_score: float | None = None
    mean_score: float | None = None
    hit_count: float | None = None
    citation_rate: float | None = None
    groundedness: float | None = None
    groundedness_n: int = 0
    canary_n: int = 0
    canary_correct: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0.0

    @property
    def canary_accuracy(self) -> float | None:
        return self.canary_correct / self.canary_n if self.canary_n else None

    def metric(self, name: str) -> float | None:
        if name == "error_rate":
            return self.error_rate
        if name == "canary_accuracy":
            return self.canary_accuracy
        value = getattr(self, name)
        return None if value is None else float(value)


class MetricDelta(BaseModel):
    baseline: float
    current: float
    delta_pct: float
    cusum: float = 0.0


class Incident(BaseModel):
    """Published by the detector to the `incidents` topic and stored in Cosmos."""

    id: str
    pk: str = "incident"
    stream: str
    status: Literal[
        "open",
        "routing",
        "investigating",
        "awaiting_human",
        "validating",
        "pr_open",
        "escalated",
        "resolved",
        "closed",
    ] = "open"
    opened_at: datetime  # event time of the first breached window
    detected_at: datetime  # wall time when the detector confirmed it
    breached_metrics: list[str]
    metric_deltas: dict[str, MetricDelta]
    window_count: int
    config_hash: str
    baseline_config_hash: str
    config_diff: list[dict[str, object]]
    recent_deploys: list[dict[str, object]]
    d1: dict[str, object] | None = None
    # what a person would look at first: response codes and which stage got slower
    error_status_counts: dict[str, int] = Field(default_factory=dict)
    stage_latency_p95_ms: dict[str, dict[str, float | None]] = Field(default_factory=dict)
    fault_label: str | None = None  # synthetic replays only (true fault id) for evals
    traceparent: str | None = None
