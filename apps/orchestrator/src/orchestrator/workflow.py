"""The incident workflow (spec 5.1.7) on Microsoft Agent Framework.

Phase 6 slice: D3 routing -> specialist RCA -> pr-writer. Every step writes an audit record,
updates the incident document and is checkpointed in Cosmos, so the run resumes after a
restart. Phase 7 adds D2, D4, the validator, D6 and human-in-the-loop branches.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import asdict
from typing import Any

from agent_framework import WorkflowBuilder, WorkflowContext, executor
from azure.cosmos.aio import ContainerProxy
from opentelemetry import trace

from driftline_common.audit import AuditWriter
from driftline_common.pricing import Pricing
from orchestrator.agents import SPECIALISTS, AgentFactory
from orchestrator.identities import AgentIdentities
from orchestrator.models import IncidentRun, RcaResult
from orchestrator.patching import ALLOWED_FILE, patch_rag_yaml
from orchestrator.prbody import render
from orchestrator.router_client import RouterClient
from orchestrator.tools import mcp_tool

log = logging.getLogger("orchestrator.workflow")
tracer = trace.get_tracer("orchestrator")
REPO_OWNER, REPO_NAME = "abh2050", "driftline"


class Deps:
    """Shared clients for the executors (module-level so executors stay plain functions)."""

    factory: AgentFactory
    identities: AgentIdentities
    router: RouterClient
    audit: AuditWriter
    incidents: ContainerProxy
    pricing: Pricing


deps = Deps()


async def save_status(run: IncidentRun, status: str, **fields: Any) -> None:
    run.status = status
    doc = await deps.incidents.read_item(run.id, partition_key="incident")
    doc.update({"status": status, "workflow": asdict(run), **fields})
    await deps.incidents.upsert_item(doc)


def tool_text_items(raw: Any) -> str:
    return "".join(str(getattr(c, "text", "") or "") for c in raw)


def evidence_context(run: IncidentRun) -> dict[str, Any]:
    inc = run.incident
    return {
        "breached_metrics": inc["breached_metrics"],
        "metric_deltas": inc["metric_deltas"],
        "window_count": inc["window_count"],
        "config_diff": inc["config_diff"],
        "recent_deploys": inc["recent_deploys"],
    }


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


@executor(id="route")
async def route(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    with tracer.start_as_current_span("orchestrator.route") as span:
        span.set_attribute("driftline.incident_id", run.id)
        context = {**evidence_context(run), "agent_health": await agent_health()}
        d3 = await deps.router.decide("D3", context, run.id)
        run.decisions["D3"] = d3
        run.costs["decisions_usd"] += float(d3.get("cost_usd", 0))
        run.specialist = d3["chosen"]
        await deps.audit.write(
            run.id, "route", "orchestrator", d3["chosen"], {"decision": d3, "context": context}
        )
        await save_status(run, "investigating")
    await ctx.send_message(run)


@executor(id="investigate")
async def investigate(run: IncidentRun, ctx: WorkflowContext[IncidentRun]) -> None:
    name = SPECIALISTS[run.specialist or "retrieval"]
    with tracer.start_as_current_span("orchestrator.investigate") as span:
        span.set_attribute("driftline.incident_id", run.id)
        span.set_attribute("gen_ai.agent.name", name)
        identity = deps.factory.identity(name)
        token = await deps.identities.token(identity)
        tools = [mcp_tool(t, token) for t in deps.factory.cfg.agents[name].tools]
        try:
            for t in tools:
                await t.__aenter__()
            agent = deps.factory.build(name, tools)
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
        usage = result.usage_details or {}  # a TypedDict
        tin = int(usage.get("input_token_count") or 0)
        tout = int(usage.get("output_token_count") or 0)
        run.costs["tokens_usd"] += deps.pricing.chat(
            deps.factory.cfg.deployment_for(name), tin, tout
        )
        run.rca = rca.model_dump()
        await deps.audit.write(
            run.id,
            "investigate",
            identity,
            "rca",
            {"agent": name, "rca": run.rca, "tokens": {"input": tin, "output": tout}},
        )
        await save_status(run, "writing_pr")
    await ctx.send_message(run)


async def call(tool: Any, name: str, run: IncidentRun, actor: str, **args: Any) -> list[str]:
    """One audited MCP tool call, returning its text items. Denials (403 from APIM) are
    audited and re-raised."""
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


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v if k not in ("content", "body") else f"<{len(str(v))} chars>")
        for k, v in args.items()
    }


@executor(id="write_pr")
async def write_pr(run: IncidentRun, ctx: WorkflowContext[IncidentRun, IncidentRun]) -> None:
    rca = RcaResult.model_validate(run.rca or {})
    if not rca.changes():
        run.notes.append("specialist proposed no config change; no PR opened")
        await save_status(run, "awaiting_human")
        await ctx.yield_output(run)
        return
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
            summary = (
                await writer.run(
                    "Write the PR summary for this incident fix.\n\n"
                    + json.dumps(
                        {"incident": evidence_context(run), "rca": run.rca, "diff": diff},
                        default=str,
                    )
                )
            ).text
            body = render(asdict(run), summary, diff, trace_id, None)
            await call(
                gh,
                "create_branch",
                run,
                identity,
                owner=REPO_OWNER,
                repo=REPO_NAME,
                branch=branch,
                from_branch="main",
            )
            await call(
                gh,
                "create_or_update_file",
                run,
                identity,
                owner=REPO_OWNER,
                repo=REPO_NAME,
                path=ALLOWED_FILE,
                content=patched,
                branch=branch,
                sha=sha,
                message=f"fix(patient-rag): {rca.root_cause[:60]} ({run.id})",
            )
            pr = await call(
                gh,
                "create_pull_request",
                run,
                identity,
                owner=REPO_OWNER,
                repo=REPO_NAME,
                title=f"Driftline: fix {run.id} ({', '.join(sorted(rca.changes()))})",
                head=branch,
                base="main",
                body=body,
            )
        run.pr_url = _pr_url(pr)
        await save_status(run, "pr_open", pr_url=run.pr_url)
    await ctx.yield_output(run)


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


def build_workflow(checkpoints: Any) -> Any:
    return (
        WorkflowBuilder(
            name="driftline-incident", start_executor=route, checkpoint_storage=checkpoints
        )
        .add_edge(route, investigate)
        .add_edge(investigate, write_pr)
        .build()
    )
