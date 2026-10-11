"""Record per-case eval rows as a Foundry evaluation run, so layers 2b and 3 appear next to
layer 1 in the project's Evaluations page (spec 11, Phase 9 acceptance). The scoring already
happened; each metric column is passed through by a code evaluator."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _passthrough(column: str) -> Any:
    def evaluator(value: Any) -> dict[str, Any]:
        return {column: float(value) if isinstance(value, (bool, int, float)) else value}

    evaluator.__name__ = f"{column}_evaluator"
    return evaluator


def upload(name: str, rows: Sequence[dict[str, Any]], metrics: Sequence[str]) -> str | None:
    """Returns the Foundry studio URL, or None when no project endpoint is configured."""
    project = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not project or not rows:
        return None
    from azure.ai.evaluation import evaluate

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "rows.jsonl"
        data.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
        result = evaluate(
            data=str(data),
            evaluators={m: _passthrough(m) for m in metrics},
            evaluator_config={m: {"column_mapping": {"value": f"${{data.{m}}}"}} for m in metrics},
            evaluation_name=name,
            azure_ai_project=project,
        )
    url = result.get("studio_url")
    return str(url) if url else None
