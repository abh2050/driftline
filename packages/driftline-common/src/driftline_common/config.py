"""Typed loaders for the YAML files in config/."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR_ENV = "DRIFTLINE_CONFIG_DIR"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RouterClientConfig(_Strict):
    kind: Literal["llm", "rules"]
    deployment: str | None = None

    @model_validator(mode="after")
    def _llm_needs_deployment(self) -> Self:
        if self.kind == "llm" and not self.deployment:
            raise ValueError("llm clients need a deployment")
        return self


class RouterConfig(_Strict):
    active: str
    escalation_threshold: float = Field(ge=0, le=1)
    dual_route_margin: float = Field(ge=0, le=1)
    clients: dict[str, RouterClientConfig]

    @model_validator(mode="after")
    def _active_is_known(self) -> Self:
        if self.active not in self.clients:
            raise ValueError(f"active client {self.active!r} is not in clients")
        return self


class AgentConfig(_Strict):
    deployment: str | None = None


class AgentDefaults(_Strict):
    deployment: str


class AgentsConfig(_Strict):
    defaults: AgentDefaults
    agents: dict[str, AgentConfig]

    def deployment_for(self, agent: str) -> str:
        return self.agents[agent].deployment or self.defaults.deployment


class DetectorConfig(_Strict):
    schedule: str
    ewma_alpha: float = Field(gt=0, le=1)
    cusum_k: float = Field(ge=0)
    cusum_h: float = Field(gt=0)
    min_breach_windows: int = Field(ge=1)
    metrics: list[str]


def config_dir() -> Path:
    """Return the config directory from the env var, or the repo's config/ folder."""
    env = os.environ.get(CONFIG_DIR_ENV)
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config"
        if (candidate / "router.yaml").is_file():
            return candidate
    raise FileNotFoundError(f"config directory not found; set {CONFIG_DIR_ENV}")


def _load[T: BaseModel](model: type[T], name: str, directory: Path | None) -> T:
    path = (directory or config_dir()) / name
    with path.open() as f:
        return model.model_validate(yaml.safe_load(f))


def load_router_config(directory: Path | None = None) -> RouterConfig:
    return _load(RouterConfig, "router.yaml", directory)


def load_agents_config(directory: Path | None = None) -> AgentsConfig:
    return _load(AgentsConfig, "agents.yaml", directory)


def load_detector_config(directory: Path | None = None) -> DetectorConfig:
    return _load(DetectorConfig, "detector.yaml", directory)
