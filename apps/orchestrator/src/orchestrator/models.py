"""Messages passed between workflow steps and the structured outputs agents return."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml
from agent_framework._workflows._checkpoint_encoding import register_checkpoint_type
from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    source: str = Field(description="metric, config_diff, file path, commit or query name")
    detail: str


class ConfigChange(BaseModel):
    key: str = Field(description="a key of apps/patient-rag/config/rag.yaml")
    value: str = Field(description="the new value as it would appear in YAML, e.g. 8, false, v3")

    @field_validator("key")
    @classmethod
    def bare_key(cls, v: str) -> str:
        """Models sometimes qualify the key with the file ("…/rag.yaml:prompt_version")."""
        return v.rsplit(":", 1)[-1].rsplit("/", 1)[-1].strip()


class RcaResult(BaseModel):
    """What a specialist returns. Strict structured output needs closed schemas, so config
    changes are a list of key/value pairs rather than a free-form object."""

    root_cause: str
    evidence: list[Evidence]
    config_changes: list[ConfigChange] = Field(
        description="settings to change; empty when the fix is a runbook or an escalation"
    )
    runbook: list[str] = Field(description="operator steps when the cause is runtime state")
    confidence: float = Field(description="0 to 1")

    def changes(self) -> dict[str, Any]:
        return {c.key: yaml.safe_load(c.value) for c in self.config_changes}


@dataclass
class IncidentRun:
    incident: dict[str, Any]
    decisions: dict[str, Any] = field(default_factory=dict)
    rca: dict[str, Any] | None = None
    specialist: str | None = None
    costs: dict[str, float] = field(
        default_factory=lambda: {"tokens_usd": 0.0, "decisions_usd": 0.0}
    )
    pr_url: str | None = None
    status: str = "routing"
    notes: list[str] = field(default_factory=list)
    specialists: list[str] = field(default_factory=list)
    rcas: dict[str, dict[str, Any]] = field(default_factory=dict)
    action: str | None = None
    escalations: list[str] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    resolution: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return str(self.incident["id"])


@dataclass
class HumanRequest:
    """What the console approvals inbox shows (runbook approval or escalation)."""

    incident_id: str
    kind: str  # "runbook" | "escalation"
    summary: str
    runbook: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class HumanResponse:
    approved: bool
    by: str
    note: str = ""


register_checkpoint_type(IncidentRun)
register_checkpoint_type(HumanRequest)
register_checkpoint_type(HumanResponse)
