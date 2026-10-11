"""Publish eval summaries to Log Analytics (DriftlineEvals_CL) for the workbook's nightly
trends view: one row per metric per run. Silent no-op without the DCR settings."""

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORTS = Path(__file__).resolve().parent / "reports"


def publish(layer: str, run_id: str, metrics: dict[str, Any], report: str = "") -> int:
    endpoint = os.environ.get("DCE_LOGS_ENDPOINT")
    dcr = os.environ.get("DECISIONS_DCR_IMMUTABLE_ID")
    if not endpoint or not dcr:
        return 0
    from azure.identity import DefaultAzureCredential
    from azure.monitor.ingestion import LogsIngestionClient

    now = datetime.now(UTC).isoformat()
    source = "ci" if os.environ.get("GITHUB_ACTIONS") else "local"
    rows: list[MutableMapping[str, Any]] = [
        {
            "TimeGenerated": now,
            "Layer": layer,
            "RunId": run_id,
            "Metric": k,
            "Value": float(v),
            "Source": source,
            "Report": report,
        }
        for k, v in metrics.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if rows:
        client = LogsIngestionClient(endpoint, DefaultAzureCredential())
        client.upload(rule_id=dcr, stream_name="Custom-DriftlineEvals", logs=rows)
    return len(rows)


def backfill() -> int:
    """Publish the summaries of the reports already in evals/reports."""
    n = 0
    for path in sorted(REPORTS.glob("*.json")):
        data = json.loads(path.read_text())
        name = path.stem
        if name.endswith("-router"):
            for client, types in data["clients"].items():
                for dtype, m in types.items():
                    n += publish(
                        "layer2a",
                        name,
                        {f"{client}.{dtype}.{k}": m[k] for k in ("accuracy", "ece", "macro_f1")},
                        path.name,
                    )
        elif name.endswith("-system"):
            n += publish("layer2b", data.get("run", name), data["summary"], path.name)
        elif name.endswith("-layer3"):
            n += publish("layer3", data.get("run", name), data["summary"], path.name)
        elif "-layer1-" in name:
            n += publish("layer1", name, data.get("metrics", {}), path.name)
    return n
