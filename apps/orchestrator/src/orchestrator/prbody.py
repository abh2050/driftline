"""Pull request body (spec 5.1.7f): root cause, cited evidence, routing scores, eval table,
cost and a trace link."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote


def trace_link(trace_id: str) -> str:
    appi = os.environ.get("APPINSIGHTS_RESOURCE_ID", "")
    tenant = os.environ.get("AZURE_TENANT_ID", "")
    query = quote(f"union OTelSpans | where TraceId == '{trace_id}'")
    return (
        f"https://portal.azure.com/#@{tenant}/resource{appi}/logs?query={query}"
        if appi
        else f"trace {trace_id}"
    )


def scores_table(decision: dict[str, Any]) -> str:
    rows = sorted(decision["scores"].items(), key=lambda kv: -kv[1])
    out = ["| Option | Score |", "|---|---|"]
    out += [f"| {'**' + k + '**' if k == decision['chosen'] else k} | {v:.3f} |" for k, v in rows]
    return "\n".join(out)


def render(
    run: dict[str, Any], summary: str, diff_lines: list[str], trace_id: str, eval_table: str | None
) -> str:
    inc = run["incident"]
    rca = run["rca"] or {}
    d3 = run["decisions"].get("D3", {})
    deltas = "\n".join(
        f"| {m} | {d['baseline']} | {d['current']} | {d['delta_pct']:+.1f}% |"
        for m, d in inc["metric_deltas"].items()
    )
    evidence = "\n".join(f"- **{e['source']}**: {e['detail']}" for e in rca.get("evidence", []))
    costs = run["costs"]
    parts = [
        summary.strip(),
        "",
        f"## Incident `{inc['id']}`",
        "",
        "| Metric | Baseline | Current | Change |",
        "|---|---|---|---|",
        deltas,
        "",
        "## Root cause",
        "",
        rca.get("root_cause", "(none)"),
        "",
        f"Confidence: {rca.get('confidence', 0):.2f} ({run.get('specialist')} specialist)",
        "",
        "### Evidence",
        "",
        evidence or "- (none)",
        "",
        "## Change",
        "",
        "```diff",
        *diff_lines,
        "```",
        "",
        f"## Routing decision (D3, `{d3.get('model', '?')}`, margin {d3.get('margin', 0):.3f})",
        "",
        scores_table(d3) if d3 else "(no routing decision)",
        "",
        "## Evaluation",
        "",
        eval_table or "Sandbox validation runs in Phase 7; this PR was not yet validated.",
        "",
        "## Cost",
        "",
        f"- Agent tokens: ${costs['tokens_usd']:.4f}",
        f"- Router decisions: ${costs['decisions_usd']:.4f}",
        "",
        f"Trace: [{trace_id}]({trace_link(trace_id)})",
        "",
        "Opened by Driftline. A human must review and merge; agents cannot merge "
        "(APIM policy + branch protection).",
    ]
    return "\n".join(parts)
