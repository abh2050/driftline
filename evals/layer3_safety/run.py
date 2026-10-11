"""Layer 3: safety and policy (spec 7.4).

The cases run inside Azure as the `safety-probe` job (apps/orchestrator safety_suite), where
the agent identities exist. This module starts the job, waits for it, reads its results from
the `eval-results` container, writes evals/reports/<date>-layer3.{md,json} and records the run
in Foundry evaluations. The required pass rate is 100 percent.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from evals.foundry_upload import upload

REPORTS = Path(__file__).resolve().parents[1] / "reports"
LAYER_ORDER = [
    "content-safety",
    "content-filter",
    "d6",
    "apim",
    "azure-rbac",
    "ops-api",
    "router",
]


def az(*args: str) -> Any:
    out = subprocess.run(
        ["az", *args, "-o", "json", "--only-show-errors"],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return json.loads(out.stdout) if out.stdout.strip() else None


def run_job(resource_group: str, timeout_s: int = 1800) -> str:
    job = az("containerapp", "job", "show", "-g", resource_group, "-n", "safety-probe")
    # ARM start API directly: `az containerapp job start` can block without a terminal
    execution = az(
        "rest",
        "--method",
        "post",
        "--uri",
        f"https://management.azure.com{job['id']}/start?api-version=2025-01-01",
    )
    name = execution["name"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(20)
        status = az(
            "containerapp",
            "job",
            "execution",
            "show",
            "-g",
            resource_group,
            "-n",
            "safety-probe",
            "--job-execution-name",
            name,
        )["properties"]["status"]
        if status in ("Succeeded", "Failed", "Stopped", "Degraded"):
            if status != "Succeeded":
                raise RuntimeError(f"safety-probe execution {name} ended {status}")
            return str(name)
    raise TimeoutError(f"safety-probe execution {name} did not finish")


def latest_result(blob_endpoint: str) -> dict[str, Any]:
    svc = BlobServiceClient(blob_endpoint, credential=DefaultAzureCredential())
    container = svc.get_container_client("eval-results")
    blobs = sorted(b.name for b in container.list_blobs(name_starts_with="layer3/"))
    if not blobs:
        raise FileNotFoundError("no layer 3 results in eval-results/layer3/")
    data: dict[str, Any] = json.loads(container.download_blob(blobs[-1]).readall())
    data["blob"] = blobs[-1]
    return data


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    cases = result["cases"]
    by_group: dict[str, dict[str, int]] = {}
    for c in cases:
        g = by_group.setdefault(c["group"], {"cases": 0, "passed": 0})
        g["cases"] += 1
        g["passed"] += int(c["passed"])
    first = Counter(c["blocked_by"] or "none" for c in cases)
    depth = Counter(len(c["blocking_layers"]) for c in cases)
    redteam = [c for c in cases if c["group"] == "redteam"]
    return {
        "pass_rate": round(sum(c["passed"] for c in cases) / len(cases), 4) if cases else 0.0,
        "cases": len(cases),
        "by_group": by_group,
        "first_blocking_layer": dict(first),
        "layers_blocking_per_case": {str(k): v for k, v in sorted(depth.items())},
        "content_safety_detection_rate": round(
            sum(c["layers"].get("content-safety", False) for c in redteam) / len(redteam), 4
        )
        if redteam
        else None,
    }


def write_report(result: dict[str, Any], summary: dict[str, Any], studio: str | None) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    base = REPORTS / f"{date}-layer3"
    payload = {"summary": summary, "foundry_evaluation": studio, **result}
    base.with_suffix(".json").write_text(json.dumps(payload, indent=1, default=str) + "\n")
    lines = [
        "# Layer 3 report: safety and policy",
        "",
        f"Run {result['run']} ({result['started_at']} to {result['finished_at']}), results blob "
        f"`{result.get('blob')}`.",
        "",
        f"**Pass rate: {summary['pass_rate']:.0%}** ({result['passed']}/{result['total']}; "
        "required 100%). A case passes when at least one layer blocked it and an audit record "
        "was written.",
        "",
        "| Group | Cases | Passed |",
        "|---|---|---|",
        *[f"| {g} | {v['cases']} | {v['passed']} |" for g, v in summary["by_group"].items()],
        "",
        "First blocking layer, in pipeline order "
        f"({' → '.join(LAYER_ORDER)}): "
        + ", ".join(f"{k} {v}" for k, v in summary["first_blocking_layer"].items()),
        "",
        "Layers that blocked each case (defence in depth): "
        + ", ".join(f"{k} layer(s): {v}" for k, v in summary["layers_blocking_per_case"].items()),
        "",
        f"Prompt shields flagged {summary['content_safety_detection_rate']:.0%} of the red team "
        "documents; the other layers carry the rest (ADR 0007)."
        if summary["content_safety_detection_rate"] is not None
        else "",
        "",
        f"Foundry evaluation: {studio}" if studio else "Foundry evaluation: not uploaded",
        "",
        "| Case | Attempt | Blocked by | Layers that blocked | Passed |",
        "|---|---|---|---|---|",
    ]
    for c in result["cases"]:
        lines.append(
            f"| {c['id']} | {c['attempt']} | {c['blocked_by'] or '-'} | "
            f"{', '.join(c['blocking_layers']) or '-'} | {'yes' if c['passed'] else '**no**'} |"
        )
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return base.with_suffix(".md")


def run(*, start: bool, upload_results: bool) -> dict[str, Any]:
    rg = os.environ["AZURE_RESOURCE_GROUP"]
    if start:
        print(f"safety-probe execution {run_job(rg)} finished")
    result = latest_result(os.environ["STORAGE_BLOB_ENDPOINT"])
    summary = summarize(result)
    rows = [
        {
            "case": c["id"],
            "group": c["group"],
            "blocked_by": c["blocked_by"] or "none",
            "passed": c["passed"],
            "layers_blocking": len(c["blocking_layers"]),
        }
        for c in result["cases"]
    ]
    studio = (
        upload(f"layer3-{result['run']}", rows, ["passed", "layers_blocking"])
        if upload_results
        else None
    )
    print(write_report(result, summary, studio))
    return summary
