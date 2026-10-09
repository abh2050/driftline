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
    # Synthetic replay only: the true fault label of the segment, None when clean.
    fault_id: str | None = None
