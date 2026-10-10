"""patient-rag configuration: config/rag.yaml plus optional fault overrides (spec 5.1.1)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

APP_DIR = Path(os.environ.get("PATIENT_RAG_HOME", Path(__file__).resolve().parents[2]))


class RagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_revision: int = 1
    chunk_size: int = Field(gt=32)
    chunk_overlap: int = Field(ge=0)
    top_k: int = Field(ge=1, le=50)
    embedding_deployment: str
    embedding_dimensions: int
    chat_deployment: str
    prompt_version: str
    reranker_enabled: bool
    reranker_deployment: str
    rerank_candidates: int = Field(ge=1)
    max_answer_tokens: int
    groundedness_sample_rate: float = Field(ge=0, le=1)
    judge_deployment: str
    index_prefix: str

    def config_hash(self) -> str:
        # config_revision is deployment metadata (bumped by fix PRs), not behavior, so it is
        # excluded: a fix PR that restores values yields the original baseline hash.
        blob = json.dumps(self.model_dump(exclude={"config_revision"}), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def index_name(self) -> str:
        """Chunking is baked into the index, so the index name follows chunk settings.

        The embedding deployment is deliberately not part of the name: changing it without
        reindexing (F2) keeps querying the old index.
        """
        return f"{self.index_prefix}-c{self.chunk_size}-o{self.chunk_overlap}"

    def prompt(self) -> str:
        return (APP_DIR / "prompts" / f"{self.prompt_version}.md").read_text()


class FaultSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


def load_base_config(path: Path | None = None) -> RagConfig:
    raw = yaml.safe_load((path or APP_DIR / "config" / "rag.yaml").read_text())
    return RagConfig.model_validate(raw)


def load_fault(fault_id: str) -> FaultSpec:
    path = APP_DIR / "faults" / f"{fault_id}.yaml"
    if not path.is_file():
        raise KeyError(fault_id)
    return FaultSpec.model_validate(yaml.safe_load(path.read_text()))


def apply(base: RagConfig, overrides: dict[str, Any]) -> RagConfig:
    return RagConfig.model_validate({**base.model_dump(), **overrides})
