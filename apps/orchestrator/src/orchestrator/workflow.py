"""The incident workflow (spec 5.1.7, 4.2) on Microsoft Agent Framework.

severity (D2) -> route (D3, dual on low margin) -> investigate -> decide_action (D4)
  pr       -> validate (sandbox-runner + D5, both must accept) -> write_pr (D6 before writes)
  runbook  -> human_gate (approval in the console, then the runbook runs)
  escalate -> human_gate (a person answers)
Any decision whose top score is below the escalation threshold sends the incident to the
human gate. Every step writes an audit record, updates the incident document and is
checkpointed in Cosmos; the human gate pauses the workflow until a response arrives.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict
from typing import Any

import httpx
from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    executor,
    handler,
    response_handler,
)
from azure.cosmos.aio import ContainerProxy
from opentelemetry import trace

from driftline_common.audit import AuditWriter
from driftline_common.auth import ServiceToken
from driftline_common.config import load_router_config
from driftline_common.dapr import DaprPublisher
from driftline_common.pricing import Pricing
from orchestrator.agents import SPECIALISTS, AgentFactory
from orchestrator.identities import AgentIdentities
from orchestrator.models import HumanRequest, HumanResponse, IncidentRun, RcaResult
from orchestrator.patching import ALLOWED_FILE, patch_rag_yaml
from orchestrator.prbody import render
from orchestrator.router_client import RouterClient
from orchestrator.safety import PromptShield, shield_middleware
from orchestrator.tools import mcp_tool

log = logging.getLogger("orchestrator.workflow")
tracer = trace.get_tracer("orchestrator")
REPO_OWNER, REPO_NAME = "abh2050", "driftline"
VALIDATION_TIMEOUT_S = int(os.environ.get("VALIDATION_TIMEOUT_S", "2400"))
# Runbooks the orchestrator may execute after a human approves them (patient-rag admin API).
RUNBOOK_ACTIONS: dict[str, list[tuple[str, str, dict[str, Any] | None]]] = {
    "index": [("POST", "/admin/indexer", {"enabled": True}), ("POST", "/admin/reindex", None)],
}


class Deps:
    factory: AgentFactory
    identities: AgentIdentities
    router: RouterClient
    audit: AuditWriter
    incidents: ContainerProxy
    pricing: Pricing
    shield: PromptShield
    publisher: DaprPublisher
    service_token: ServiceToken
    validations: dict[str, asyncio.Future[dict[str, Any]]]


deps = Deps()


def tool_text_items(raw: Any) -> str:
    return "".join(str(getattr(c, "text", "") or "") for c in raw)


async def save_status(run: IncidentRun, status: str, **fields: Any) -> None:
    run.status = status
    doc = await deps.incidents.read_item(run.id, partition_key="incident")
    doc.update({"status": status, "workflow": asdict(run), **fields})
    await deps.incidents.upsert_item(doc)


def evidence_context(run: IncidentRun) -> dict[str, Any]:
    inc = run.incident
    return {
        "breached_metrics": inc["breached_metrics"],
        "metric_deltas": inc["metric_deltas"],
        "window_count": inc["window_count"],
        "config_diff": inc["config_diff"],
        "recent_deploys": inc["recent_deploys"],
        "error_status_counts": inc.get("error_status_counts", {}),
        "stage_latency_p95_ms": inc.get("stage_latency_p95_ms", {}),
    }


async def decide(run: IncidentRun, dtype: str, context: dict[str, Any]) -> dict[str, Any]:
    """One router decision: recorded, costed, audited; low confidence flags an escalation."""
    d = await deps.router.decide(dtype, context, run.id)
    run.decisions[dtype] = d
    run.costs["decisions_usd"] += float(d.get("cost_usd", 0))
    if d.get("escalated"):
        run.escalations.append(
            f"{dtype} confidence {max(d['scores'].values()):.2f} is below the escalation threshold"
        )
    await deps.audit.write(
        run.id, f"decision-{dtype}", "router", d["chosen"], {"decision": d, "context": context}
    )
    return d


async def agent_health() -> dict[str, Any]:
    """Live agent health over the last 15 minutes, through kql-tool (spec 4.2 D3)."""
    token = await deps.identities.token(deps.factory.identity("explainer"))
    try:
        async with mcp_tool("kql", token) as kql:
            raw = await kql.call_tool("run_query", name="agent_health", params={"minutes": 15})
        data = json.loads(raw if isinstance(raw, str) else tool_text_items(raw))
        cols = data.get("columns", [])
        return {row[0]: dict(zip(cols[1:], row[1:], strict=False)) for row in data.get("rows", [])}
    except Exception as e:
        log.warning("agent health unavailable: %s", e)
        return {}


@executor(id="severity")
async def severity(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    with tracer.start_as_current_span("orchestrator.severity") as span:
        span.set_attribute("driftline.incident_id", run.id)
        d2 = await decide(run, "D2", evidence_context(run))
        await save_status(run, "routing", severity=d2["chosen"])
    await ctx.send_message(run)


@executor(id="route")
async def route(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    with tracer.start_as_current_span("orchestrator.route") as span:
        span.set_attribute("driftline.incident_id", run.id)
        context = {**evidence_context(run), "investigator_agent_health": await agent_health()}
        d3 = await decide(run, "D3", context)
        ranked = sorted(d3["scores"].items(), key=lambda kv: -kv[1])
        run.specialists = [ranked[0][0]]
        if d3["margin"] < load_router_config().dual_route_margin and len(ranked) > 1:
            run.specialists.append(ranked[1][0])  # dual routing (spec 4.2 D3)
        span.set_attribute("driftline.specialists", ",".join(run.specialists))
        await save_status(run, "investigating")
    await ctx.send_message(run)


async def run_specialist(run: IncidentRun, specialist: str) -> tuple[RcaResult, float]:
    name = SPECIALISTS[specialist]
    with tracer.start_as_current_span("orchestrator.investigate") as span:
        span.set_attribute("driftline.incident_id", run.id)
        span.set_attribute("gen_ai.agent.name", name)
        identity = deps.factory.identity(name)
        token = await deps.identities.token(identity)
        tools = [mcp_tool(t, token) for t in deps.factory.cfg.agents[name].tools]
        try:
            for t in tools:
                await t.__aenter__()
            agent = deps.factory.build(
                name,
                tools,
                middleware=[shield_middleware(deps.shield, deps.audit, run.id, identity)],
            )
            prompt = "Investigate this incident and return the root cause.\n\n" + json.dumps(
                evidence_context(run), indent=1, default=str
            )
            result = await agent.run(prompt, options={"response_format": RcaResult})
        finally:
            for t in tools:
                await t.close()
        rca = (
            result.value
            if isinstance(result.value, RcaResult)
            else RcaResult.model_validate_json(result.text)
        )
        usage = result.usage_details or {}
        tin = int(usage.get("input_token_count") or 0)
        tout = int(usage.get("output_token_count") or 0)
        cost = deps.pricing.chat(deps.factory.cfg.deployment_for(name), tin, tout)
        await deps.audit.write(
            run.id,
            "investigate",
            identity,
            "rca",
            {"agent": name, "rca": rca.model_dump(), "tokens": {"input": tin, "output": tout}},
        )
        return rca, cost


@executor(id="investigate")
async def investigate(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    results = await asyncio.gather(*(run_specialist(run, s) for s in run.specialists))
    for s, (rca, cost) in zip(run.specialists, results, strict=True):
        run.rcas[s] = rca.model_dump()
        run.costs["tokens_usd"] += cost
    best = max(run.specialists, key=lambda s: run.rcas[s]["confidence"])  # dual: keep the best
    run.specialist = best
    run.rca = run.rcas[best]
    await save_status(run, "deciding_action")
    await ctx.send_message(run)


@executor(id="decide_action")
async def decide_action(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    rca = RcaResult.model_validate(run.rca or {})
    await decide(
        run,
        "D4",
        {
            **evidence_context(run),
            "specialist": run.specialist,
            "root_cause": rca.root_cause,
            "proposed_config_changes": rca.changes(),
            "proposed_runbook": rca.runbook,
            "specialist_confidence": rca.confidence,
        },
    )
    run.action = "escalate" if run.escalations else run.decisions["D4"]["chosen"]
    if run.action == "pr" and not rca.changes():
        run.action = "escalate"
        run.escalations.append("D4 chose pr but the specialist proposed no config change")
    await save_status(
        run, "validating" if run.action == "pr" else "awaiting_human", action=run.action
    )
    await ctx.send_message(run)


def eval_rows(validation: dict[str, Any], incident: dict[str, Any]) -> str:
    names = [
        "recall_at_k",
        "mrr",
        "groundedness",
        "relevance",
        "response_completeness",
        "citation_rate",
        "answer_accuracy",
        "latency_p95_ms",
        "cost_per_query_usd",
    ]
    gate = validation.get("gate", {})
    out = [
        f"Layer 1 on {validation.get('questions', '?')} golden questions in the sandbox "
        f"(target metric `{validation.get('target_metric')}`, gate "
        f"{'**passed**' if gate.get('passed') else '**failed**'}).",
        "",
        "| Metric | Baseline | Candidate (this PR) |",
        "|---|---|---|",
    ]
    for n in names:
        b, c = validation.get("baseline", {}).get(n), validation.get("candidate", {}).get(n)
        if b is not None and c is not None:
            out.append(f"| {n} | {b} | {c} |")
    obs = incident.get("metric_deltas", {})
    if obs:
        out += [
            "",
            "Observed during the incident (telemetry): "
            + ", ".join(f"{m} {d['baseline']} -> {d['current']}" for m, d in obs.items()),
        ]
    return "\n".join(out)


async def running_overrides(incident: dict[str, Any]) -> dict[str, Any]:
    """Settings the running patient-rag differed from baseline by when the incident opened,
    from its config registry (the same lookup the detector uses for config_diff)."""
    base = os.environ.get("PATIENT_RAG_URL", "http://patient-rag")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(f"{base}/configs", headers=await deps.service_token.header())
            resp.raise_for_status()
            registry = resp.json()
    except httpx.HTTPError as e:
        log.warning("config registry unavailable: %s", e)
        return {}
    baseline: dict[str, Any] = next(
        (v["config"] for v in registry.values() if v["label"] == "baseline"), {}
    )
    current = registry.get(str(incident.get("config_hash")), {}).get("config", {})
    return {k: v for k, v in current.items() if k != "config_revision" and baseline.get(k) != v}


@executor(id="validate")
async def validate(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    rca = RcaResult.model_validate(run.rca or {})
    run_id = uuid.uuid4().hex[:10]
    with tracer.start_as_current_span("orchestrator.validate") as span:
        span.set_attribute("driftline.incident_id", run.id)
        span.set_attribute("gen_ai.agent.name", "validator")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        deps.validations[run_id] = fut
        await deps.publisher.publish(
            "sandbox",
            {
                "run_id": run_id,
                "incident_id": run.id,
                "changes": rca.changes(),
                "running": await running_overrides(run.incident),
                "breached_metrics": run.incident["breached_metrics"],
            },
        )
        await deps.audit.write(
            run.id,
            "sandbox-request",
            deps.factory.identity("validator"),
            "sent",
            {"run_id": run_id, "changes": rca.changes()},
        )
        try:
            validation = await asyncio.wait_for(fut, VALIDATION_TIMEOUT_S)
        except TimeoutError:
            validation = {"error": "sandbox validation timed out", "gate": {"passed": False}}
        finally:
            deps.validations.pop(run_id, None)
        run.validation = {k: v for k, v in validation.items() if k != "rows"}
        diff = f"{ALLOWED_FILE}: " + ", ".join(f"{k} -> {v}" for k, v in rca.changes().items())
        gate_summary = {k: v for k, v in validation.get("gate", {}).items() if k != "details"}
        d5 = await decide(
            run,
            "D5",
            {
                "eval": {
                    "target_metric": validation.get("target_metric"),
                    "baseline": validation.get("baseline"),
                    "candidate": validation.get("candidate"),
                    "gate": gate_summary,
                    "error": validation.get("error"),
                },
                "patch_diff": diff,
            },
        )
        accepted = d5["chosen"] == "accept" and bool(gate_summary.get("passed"))
        if not accepted:
            run.action = "escalate"
            run.escalations.append(
                f"fix not accepted (gate passed={gate_summary.get('passed')}, D5={d5['chosen']})"
                + (f": {validation['error']}" if validation.get("error") else "")
            )
        await save_status(
            run, "writing_pr" if accepted else "awaiting_human", validation=run.validation
        )
    await ctx.send_message(run)


async def call(tool: Any, name: str, run: IncidentRun, actor: str, **args: Any) -> list[str]:
    """One audited MCP tool call, returning its text items. Denials are audited and raised."""
    try:
        raw = await tool.call_tool(name, **args)
        texts = [raw] if isinstance(raw, str) else [str(getattr(c, "text", "") or "") for c in raw]
        await deps.audit.write(run.id, f"tool-{name}", actor, "ok", {"args": _redact(args)})
        return texts
    except Exception as e:
        await deps.audit.write(
            run.id,
            f"tool-{name}",
            actor,
            "denied_or_failed",
            {"args": _redact(args), "error": str(e)[:500]},
        )
        raise


async def precheck(
    run: IncidentRun, tool: str, args: dict[str, Any], files_changed: list[str]
) -> bool:
    """D6: the router judges every write before it is attempted (APIM still enforces).
    files_changed is every path the branch changes, from the patch code, not the model."""
    d6 = await decide(
        run,
        "D6",
        {
            "agent": "pr-writer",
            "tool": tool,
            "arguments": _redact(args),
            "files_changed": files_changed,
        },
    )
    if d6["chosen"] != "allow":
        run.escalations.append(f"D6 {d6['chosen']} for {tool}")
        return False
    return True


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v if k not in ("content", "body") else f"<{len(str(v))} chars>")
        for k, v in args.items()
    }


@executor(id="write_pr")
async def write_pr(run: IncidentRun, ctx: WorkflowContext[IncidentRun, IncidentRun]) -> None:
    rca = RcaResult.model_validate(run.rca or {})
    with tracer.start_as_current_span("orchestrator.write_pr") as span:
        span.set_attribute("driftline.incident_id", run.id)
        span.set_attribute("gen_ai.agent.name", "pr-writer")
        trace_id = format(span.get_span_context().trace_id, "032x")
        identity = deps.factory.identity("pr-writer")
        token = await deps.identities.token(identity)
        branch = f"driftline/fix-{run.id}"
        async with mcp_tool("github_write", token) as gh:
            current = await call(
                gh,
                "get_file_contents",
                run,
                identity,
                owner=REPO_OWNER,
                repo=REPO_NAME,
                path=ALLOWED_FILE,
                ref="refs/heads/main",
            )
            text, sha = _file_and_sha(current)
            patched = patch_rag_yaml(text, rca.changes(), run.id)
            diff = list(
                difflib.unified_diff(
                    text.splitlines(),
                    patched.splitlines(),
                    f"a/{ALLOWED_FILE}",
                    f"b/{ALLOWED_FILE}",
                    lineterm="",
                )
            )
            writer = deps.factory.build("pr-writer")
            summary = await writer.run(
                "Write the PR summary for this incident fix.\n\n"
                + json.dumps(
                    {"incident": evidence_context(run), "rca": run.rca, "diff": diff}, default=str
                )
            )
            usage = summary.usage_details or {}
            run.costs["tokens_usd"] += deps.pricing.chat(
                deps.factory.cfg.deployment_for("pr-writer"),
                int(usage.get("input_token_count") or 0),
                int(usage.get("output_token_count") or 0),
            )
            writes: list[tuple[str, dict[str, Any]]] = [
                (
                    "create_branch",
                    {
                        "owner": REPO_OWNER,
                        "repo": REPO_NAME,
                        "branch": branch,
                        "from_branch": "main",
                    },
                ),
                (
                    "create_or_update_file",
                    {
                        "owner": REPO_OWNER,
                        "repo": REPO_NAME,
                        "path": ALLOWED_FILE,
                        "content": patched,
                        "branch": branch,
                        "sha": sha,
                        "message": f"fix(patient-rag): {rca.root_cause[:60]} ({run.id})",
                    },
                ),
            ]
            allowed = True
            for tool, args in writes:
                if not await precheck(run, tool, args, [ALLOWED_FILE]):
                    allowed = False
                    break
                await call(gh, tool, run, identity, **args)
            if allowed:
                body = render(
                    asdict(run),
                    summary.text,
                    diff,
                    trace_id,
                    eval_rows(run.validation or {}, run.incident),
                )
                pr_args = {
                    "owner": REPO_OWNER,
                    "repo": REPO_NAME,
                    "head": branch,
                    "base": "main",
                    "body": body,
                    "title": f"Driftline: fix {run.id} ({', '.join(sorted(rca.changes()))})",
                }
                if await precheck(run, "create_pull_request", pr_args, [ALLOWED_FILE]):
                    run.pr_url = _pr_url(
                        await call(gh, "create_pull_request", run, identity, **pr_args)
                    )
        if run.pr_url:
            await save_status(run, "pr_open", pr_url=run.pr_url)
            await ctx.yield_output(run)
            return
        run.action = "escalate"
        await save_status(run, "awaiting_human")
    await ctx.send_message(run)


class HumanGate(Executor):
    """Pauses the workflow until a person answers in the console (spec 4.2, 5.2c)."""

    def __init__(self) -> None:
        super().__init__(id="human_gate")

    @handler
    async def ask(self, run: IncidentRun, ctx: WorkflowContext[IncidentRun, IncidentRun]) -> None:
        rca = run.rca or {}
        kind = "runbook" if run.action == "runbook" and not run.escalations else "escalation"
        request = HumanRequest(
            incident_id=run.id,
            kind=kind,
            summary=str(rca.get("root_cause", "No root cause was established.")),
            runbook=list(rca.get("runbook", [])),
            reasons=list(run.escalations),
        )
        await deps.audit.write(run.id, "human-gate", "orchestrator", kind, asdict(request))
        await save_status(run, "awaiting_human", pending=asdict(request))
        await ctx.request_info(request, HumanResponse)

    @response_handler
    async def answered(
        self,
        original: HumanRequest,
        response: HumanResponse,
        ctx: WorkflowContext[IncidentRun, IncidentRun],
    ) -> None:
        doc = await deps.incidents.read_item(original.incident_id, partition_key="incident")
        fields = IncidentRun.__dataclass_fields__
        run = IncidentRun(**{k: v for k, v in doc["workflow"].items() if k in fields})
        run.resolution = {
            "approved": response.approved,
            "by": response.by,
            "note": response.note,
            "kind": original.kind,
        }
        await deps.audit.write(
            run.id,
            "human-response",
            response.by,
            "approved" if response.approved else "rejected",
            run.resolution,
        )
        status = "closed"
        if original.kind == "runbook" and response.approved:
            status = await execute_runbook(run)
        elif original.kind == "escalation":
            status = "escalated" if response.approved else "closed"
        await save_status(run, status, pending=None, resolution=run.resolution)
        await ctx.yield_output(run)


async def execute_runbook(run: IncidentRun) -> str:
    actions = RUNBOOK_ACTIONS.get(run.specialist or "", [])
    if not actions:
        run.notes.append("approved runbook has no automated actions; an operator follows it")
        return "runbook_approved"
    base = os.environ.get("PATIENT_RAG_URL", "http://patient-rag")
    async with httpx.AsyncClient(timeout=60) as http:
        for method, path, body in actions:
            resp = await http.request(
                method, f"{base}{path}", json=body, headers=await deps.service_token.header()
            )
            await deps.audit.write(
                run.id,
                "runbook-step",
                "orchestrator",
                str(resp.status_code),
                {"path": path, "body": body},
            )
            resp.raise_for_status()
    return "runbook_executed"


def _file_and_sha(items: list[str]) -> tuple[str, str]:
    """get_file_contents returns a status item with the blob SHA and the file text."""
    sha = ""
    for t in items:
        if m := re.search(r"SHA: ([0-9a-f]{40})", t):
            sha = m.group(1)
    body = next((t for t in items if not t.startswith("successfully downloaded")), "")
    if not body or not sha:
        raise ValueError("unexpected get_file_contents result")
    return body, sha


def _pr_url(items: list[str]) -> str:
    text = "\n".join(items)
    if m := re.search(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", text):
        return m.group(0)
    try:
        data = json.loads(items[0])
        return str(data.get("html_url") or data.get("url"))
    except (json.JSONDecodeError, IndexError, AttributeError):
        return text[:200]


def build_workflow(checkpoints: Any, incident_id: str) -> Any:
    gate = HumanGate()
    return (
        WorkflowBuilder(
            name=f"driftline-{incident_id}", start_executor=severity, checkpoint_storage=checkpoints
        )
        .add_edge(severity, route)
        .add_edge(route, investigate)
        .add_edge(investigate, decide_action)
        .add_edge(decide_action, validate, condition=lambda r: r.action == "pr")
        .add_edge(decide_action, gate, condition=lambda r: r.action != "pr")
        .add_edge(validate, write_pr, condition=lambda r: r.action == "pr")
        .add_edge(validate, gate, condition=lambda r: r.action != "pr")
        .add_edge(write_pr, gate)
        .build()
    )
