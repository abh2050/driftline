"""Layer 2b: the end-to-end system on replayed faults (spec 7.3).

For each fault and trial, a replay runs the fault segment on its own stream
(`replay-eval-<fault>-t<trial>-<run>`). The orchestrator treats `replay-eval-*` streams as dry
runs: the whole workflow runs, including the sandbox and D6 on every write, but no pull request
is opened (ADR 0009). A clean replay measures false positives.

Measured per incident against evals/incidents/F#.json: detection, time to detect (event time
from the fault's onset), routing, RCA top-1 (exact label match, plus a rubric graded by the
router model and by the judge model with their agreement), action, fix acceptance, cost
(tokens, decisions, sandbox) and wall time.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential

from evals.foundry_upload import upload
from evals.trends import publish
from scripts.replay import start as start_replay

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "evals" / "incidents"
TELEMETRY = ROOT / "data" / "telemetry" / "faults"
REPORTS = ROOT / "evals" / "reports"
FAULTS = [f"F{i}" for i in range(1, 8)]
SETTLED = {"pr_dry_run", "pr_open", "awaiting_human", "escalated", "closed", "failed"}
DETECT_TIMEOUT_S = 15 * 60
SETTLE_TIMEOUT_S = 50 * 60
JUDGES = {"router": "gpt-5-mini", "judge": "gpt-5.4-mini"}


def label(fault: str) -> dict[str, Any]:
    return dict(json.loads((LABELS / f"{fault}.json").read_text()))


def onset(fault: str, trial: int) -> datetime:
    with gzip.open(TELEMETRY / f"{fault}-t{trial}.jsonl.gz", "rt") as f:
        for line in f:
            e = json.loads(line)
            if e.get("fault_id"):
                return datetime.fromisoformat(e["ts"])
    raise ValueError(f"no fault events in {fault}-t{trial}")


async def incidents_on(container: Any, stream: str) -> list[dict[str, Any]]:
    return [
        d
        async for d in container.query_items(
            "SELECT * FROM c WHERE c.stream = @s",
            parameters=[{"name": "@s", "value": stream}],
            partition_key="incident",
        )
    ]


async def wait_for(container: Any, streams: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    """Wait until every fault stream has its incident settled (or times out)."""
    t0 = time.time()
    out: dict[str, list[dict[str, Any]]] = {}
    while True:
        done = True
        for key, stream in streams.items():
            docs = await incidents_on(container, stream)
            out[key] = docs
            fault = key.split(":")[0]
            mine = [d for d in docs if fault in (d.get("fault_label") or "")]
            waiting_to_detect = not mine and time.time() - t0 < DETECT_TIMEOUT_S
            if waiting_to_detect or any(d.get("status") not in SETTLED for d in mine):
                done = False
        if done or time.time() - t0 > SETTLE_TIMEOUT_S:
            return out
        print(
            f"  waiting: {sum(bool(v) for v in out.values())}/{len(streams)} detected, "
            f"{int(time.time() - t0)}s",
            flush=True,
        )
        await asyncio.sleep(30)


def changes(rca: dict[str, Any] | None) -> dict[str, Any]:
    if not rca:
        return {}
    out = {}
    for c in rca.get("config_changes", []):
        key = str(c["key"]).rsplit(":", 1)[-1].rsplit("/", 1)[-1].strip()
        out[key] = yaml.safe_load(c["value"])
    return out


def exact_match(lab: dict[str, Any], doc: dict[str, Any]) -> bool:
    wf = doc.get("workflow") or {}
    if wf.get("specialist") != lab["specialist"]:
        return False
    rca = wf.get("rca") or {}
    fix = lab.get("expected_fix", {})
    if lab["action"] == "pr":
        want = fix.get("set", {})
        got = changes(rca)
        return all(got.get(k) == v for k, v in want.items())
    if lab["action"] == "runbook":
        return bool(rca.get("runbook"))
    return not changes(rca)  # escalate: nothing in the repository to change


async def judge_rca(lab: dict[str, Any], doc: dict[str, Any]) -> dict[str, str]:
    """Two graders on the same rubric; agreement is reported (spec 7.3)."""
    from driftline_common.foundry import make_openai_client

    rca = (doc.get("workflow") or {}).get("rca") or {}
    prompt = (
        "Grade a root cause analysis against the known cause of an incident.\n"
        f"Known cause: {lab['root_cause']}\n"
        f"Known fix: {json.dumps(lab.get('expected_fix'))}\n"
        f"Analysis: {rca.get('root_cause', '(none)')}\n"
        f"Proposed changes: {json.dumps(changes(rca))}; "
        f"runbook: {json.dumps(rca.get('runbook', []))}\n"
        'Answer JSON {"grade": "correct" | "partial" | "incorrect"}: correct when it names the '
        "same cause and a fix that would work, partial when the cause is right but the fix is "
        "missing or wrong, incorrect otherwise."
    )
    cred = DefaultAzureCredential()
    client = make_openai_client(credential=cred, max_retries=6)
    out: dict[str, str] = {}
    try:
        for name, model in JUDGES.items():
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            try:
                out[name] = json.loads(resp.choices[0].message.content or "{}").get("grade", "?")
            except json.JSONDecodeError:
                out[name] = "?"
    finally:
        await client.close()
        await cred.close()
    return out


def score(fault: str, trial: int, docs: list[dict[str, Any]]) -> dict[str, Any]:
    lab = label(fault)
    mine = sorted(
        (d for d in docs if fault in (d.get("fault_label") or "")),
        key=lambda d: d.get("detected_at", ""),
    )
    row: dict[str, Any] = {
        "fault": fault,
        "trial": trial,
        "detected": bool(mine),
        "other_incidents": len(docs) - len(mine),
    }
    if not mine:
        return row
    doc = mine[0]
    wf = doc.get("workflow") or {}
    dec = wf.get("decisions") or {}
    start = onset(fault, trial)
    opened = datetime.fromisoformat(str(doc["opened_at"]).replace("Z", "+00:00"))
    detected_event = opened + timedelta(minutes=int(doc.get("window_count", 0)))
    val = doc.get("validation") or {}
    cand = val.get("candidate") or {}
    sandbox_usd = float(cand.get("cost_per_query_usd", 0)) * float(cand.get("questions", 0))
    costs = wf.get("costs") or {}
    detected_wall = datetime.fromisoformat(str(doc["detected_at"]).replace("Z", "+00:00"))
    settled_wall = datetime.fromtimestamp(int(doc["_ts"]), UTC)
    action = wf.get("action")
    row.update(
        incident=doc["id"],
        status=doc.get("status"),
        time_to_detect_min=round((detected_event - start).total_seconds() / 60, 1),
        severity=dec.get("D2", {}).get("chosen"),
        severity_ok=dec.get("D2", {}).get("chosen") == lab["severity"],
        specialists=wf.get("specialists", []),
        routing_ok=(wf.get("specialists") or [None])[0] == lab["specialist"],
        routing_lenient=lab["specialist"] in (wf.get("specialists") or []),
        rca_exact=exact_match(lab, doc),
        action=action,
        action_ok=action == lab["action"],
        d4=dec.get("D4", {}).get("chosen"),
        gate_passed=(val.get("gate") or {}).get("passed"),
        d5=dec.get("D5", {}).get("chosen"),
        fix_accepted=doc.get("status") in ("pr_dry_run", "pr_open"),
        tokens_usd=round(float(costs.get("tokens_usd", 0)), 5),
        decisions_usd=round(float(costs.get("decisions_usd", 0)), 5),
        sandbox_usd=round(sandbox_usd, 5),
        wall_time_s=round((settled_wall - detected_wall).total_seconds()),
        escalations=wf.get("escalations", []),
    )
    return row


def aggregate(rows: list[dict[str, Any]], clean: dict[str, Any] | None) -> dict[str, Any]:
    det = [r for r in rows if r["detected"]]

    def rate(xs: list[Any]) -> float | None:
        return round(sum(1 for x in xs if x) / len(xs), 4) if xs else None

    pr_rows = [r for r in det if label(r["fault"])["action"] == "pr"]
    graded = [r for r in det if "rca_grades" in r]
    pts = {"correct": 1.0, "partial": 0.5}
    return {
        "trials": len(rows),
        "detection_recall": rate([r["detected"] for r in rows]),
        "time_to_detect_min_median": sorted(r["time_to_detect_min"] for r in det)[len(det) // 2]
        if det
        else None,
        "severity_accuracy": rate([r["severity_ok"] for r in det]),
        "routing_accuracy": rate([r["routing_ok"] for r in det]),
        "routing_accuracy_lenient": rate([r["routing_lenient"] for r in det]),
        "rca_top1_exact": rate([r["rca_exact"] for r in det]),
        "rca_judge_router": round(
            sum(pts.get(r["rca_grades"]["router"], 0) for r in graded) / len(graded), 4
        )
        if graded
        else None,
        "rca_judge_model": round(
            sum(pts.get(r["rca_grades"]["judge"], 0) for r in graded) / len(graded), 4
        )
        if graded
        else None,
        "rca_judge_agreement": rate(
            [r["rca_grades"]["router"] == r["rca_grades"]["judge"] for r in graded]
        ),
        "correct_action_rate": rate([r["action_ok"] for r in det]),
        "fix_acceptance_rate": rate([r["fix_accepted"] for r in pr_rows]),
        "cost_per_incident_usd": round(
            sum(r["tokens_usd"] + r["decisions_usd"] + r["sandbox_usd"] for r in det) / len(det), 4
        )
        if det
        else None,
        "token_cost_per_incident_usd": round(sum(r["tokens_usd"] for r in det) / len(det), 5)
        if det
        else None,
        "decision_cost_per_incident_usd": round(sum(r["decisions_usd"] for r in det) / len(det), 5)
        if det
        else None,
        "wall_time_s_median": sorted(r["wall_time_s"] for r in det)[len(det) // 2] if det else None,
        "false_positives": clean,
    }


def write_report(summary: dict[str, Any], rows: list[dict[str, Any]], meta: dict[str, Any]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    base = REPORTS / f"{date}-system"
    base.with_suffix(".json").write_text(
        json.dumps({"summary": summary, "rows": rows, **meta}, indent=1, default=str) + "\n"
    )
    fp = summary.get("false_positives") or {}
    lines = [
        "# Layer 2b report: end-to-end system",
        "",
        f"Run `{meta['run']}`: {meta['trials_per_fault']} trial(s) per fault at {meta['speed']}x "
        "replay speed; pull requests in dry-run (ADR 0009).",
        "",
        "| Metric | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in summary.items() if k != "false_positives"],
        f"| false positives | {fp.get('incidents', '-')} in {fp.get('hours', '-')} h of clean "
        "replay |",
        "",
        f"Foundry evaluation: {meta.get('foundry_evaluation') or 'not uploaded'}",
        "",
        "| Fault | Trial | Detected | TTD (min) | Severity | Specialist | RCA exact | RCA grades "
        "(router / judge) | Action | Fix accepted | Cost $ | Wall s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if not r["detected"]:
            lines.append(f"| {r['fault']} | {r['trial']} | **no** | | | | | | | | | |")
            continue
        g = r.get("rca_grades", {})
        cost = r["tokens_usd"] + r["decisions_usd"] + r["sandbox_usd"]
        lines.append(
            f"| {r['fault']} | {r['trial']} | yes | {r['time_to_detect_min']} | {r['severity']} | "
            f"{','.join(r['specialists'])}{'' if r['routing_ok'] else ' ✗'} | "
            f"{'yes' if r['rca_exact'] else 'no'} | {g.get('router', '-')} / {g.get('judge', '-')} "
            f"| {r['action']}{'' if r['action_ok'] else ' ✗'} | "
            f"{'yes' if r['fix_accepted'] else '-'} | {cost:.3f} | {r['wall_time_s']} |"
        )
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return base.with_suffix(".md")


def save_progress(run_id: str, rows: list[dict[str, Any]]) -> None:
    """Rows so far, so a late failure loses nothing (resume with --rescore)."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f".layer2b-{run_id}.partial.json").write_text(json.dumps(rows, default=str))


async def run(
    trials: int,
    faults: list[str],
    clean_hours: int,
    speed: str,
    upload_results: bool,
    rescore: str | None = None,
) -> dict[str, Any]:
    """rescore: score an earlier run's incidents from Cosmos (its run id) without replaying."""
    run_id = rescore or uuid.uuid4().hex[:6]
    rg = os.environ["AZURE_RESOURCE_GROUP"]
    cred = DefaultAzureCredential()
    rows: list[dict[str, Any]] = []
    clean: dict[str, Any] | None = None
    async with CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=cred) as cosmos:
        container = cosmos.get_database_client("driftline").get_container_client("incidents")
        for trial in range(1, trials + 1):
            if rescore:
                streams = {
                    f"{f}:{trial}": f"replay-eval-{f.lower()}-t{trial}-{run_id}" for f in faults
                }
            else:
                streams = {
                    f"{f}:{trial}": start_replay(
                        f"fault:{f}:{trial}", f"eval-{f.lower()}-t{trial}-{run_id}", speed, rg
                    )
                    for f in faults
                }
                print(f"trial {trial}: started {', '.join(streams.values())}", flush=True)
            found = await wait_for(container, streams)
            for key, docs in found.items():
                fault, t = key.split(":")
                row = score(fault, int(t), docs)
                if row["detected"]:
                    doc = next(d for d in docs if d["id"] == row["incident"])
                    row["rca_grades"] = await judge_rca(label(fault), doc)
                rows.append(row)
                print(json.dumps({k: row.get(k) for k in ("fault", "trial", "detected", "action")}))
            save_progress(run_id, rows)
        if clean_hours:
            stream = start_replay(f"clean:{clean_hours}", f"eval-clean-{run_id}", speed, rg)
            # the detector needs the replay to finish plus a few ticks
            await asyncio.sleep(clean_hours * 3600 / float(speed) + 300)
            docs = await incidents_on(container, stream)
            clean = {"hours": clean_hours, "incidents": len(docs), "stream": stream}
    await cred.close()
    summary = aggregate(rows, clean)
    metrics = ["detected", "routing_ok", "rca_exact", "action_ok", "fix_accepted"]
    studio = (
        upload(
            f"layer2b-{run_id}",
            [{k: r.get(k, False) for k in ["fault", "trial", *metrics]} for r in rows],
            metrics,
        )
        if upload_results
        else None
    )
    meta = {
        "run": run_id,
        "trials_per_fault": trials,
        "speed": speed,
        "foundry_evaluation": studio,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    path = write_report(summary, rows, meta)
    publish("layer2b", run_id, summary, path.name)
    print(path)
    return summary
