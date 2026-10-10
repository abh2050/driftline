"""Token and rerank cost attribution from config/pricing.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from driftline_common.config import config_dir


@dataclass(frozen=True)
class Pricing:
    table: dict[str, dict[str, float]]

    @classmethod
    def load(cls, directory: Path | None = None) -> Pricing:
        raw: dict[str, Any] = yaml.safe_load(
            ((directory or config_dir()) / "pricing.yaml").read_text()
        )
        return cls(
            {k: {kk: float(vv) for kk, vv in v.items()} for k, v in raw["deployments"].items()}
        )

    def chat(self, deployment: str, tokens_in: int, tokens_out: int) -> float:
        p = self.table.get(deployment, {})
        return (tokens_in * p.get("input", 0.0) + tokens_out * p.get("output", 0.0)) / 1e6

    def rerank(self, deployment: str, searches: int) -> float:
        return searches * self.table.get(deployment, {}).get("per_1k_searches", 0.0) / 1000
