"""Generate infra/workbooks/driftline.json (spec 8.2) from readable KQL.

uv run python -m scripts.gen_workbook
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRICES = yaml.safe_load((ROOT / "config" / "pricing.yaml").read_text())["deployments"]
PRICE_TABLE = ", ".join(
    f'"{m}", {p["input"]}, {p["output"]}' for m, p in PRICES.items() if "input" in p
)

INCIDENT_SPANS = """OTelSpans
| where TimeGenerated {TimeRange}
| extend Incident = tostring(Attributes["driftline.incident_id"])
| where isnotempty(Incident)"""

Q = {
    "incidents": INCIDENT_SPANS
    + """
| summarize Last = max(TimeGenerated) by Incident
| order by Last desc
| project value = Incident, label = Incident""",
    "timeline": INCIDENT_SPANS
    + """
| where Name in ("detector.incident", "orchestrator.workflow", "orchestrator.severity",
    "orchestrator.route", "orchestrator.investigate", "orchestrator.validate",
    "orchestrator.write_pr", "orchestrator.resume", "ops-api.respond")
| summarize Start = min(TimeGenerated), End = max(EndTime) by Incident, Step = Name
| extend Seconds = round(datetime_diff("millisecond", End, Start) / 1000.0, 1)
| order by Incident desc, Start asc""",
    "waterfall": """let traces = OTelSpans
| where TimeGenerated {TimeRange}
| where tostring(Attributes["driftline.incident_id"]) == "{Incident}"
| distinct TraceId;
let spans = OTelSpans
| where TimeGenerated {TimeRange}
| where TraceId in (traces);
let t0 = toscalar(spans | summarize min(TimeGenerated));
spans
| extend StartS = round(datetime_diff("millisecond", TimeGenerated, t0) / 1000.0, 2),
    DurationS = round(DurationMs / 1000.0, 2)
| project StartS, DurationS, Service = ServiceName, Span = Name, Status = StatusCode,
    Agent = tostring(Attributes["gen_ai.agent.name"]), TraceId, SpanId, ParentSpanId
| order by StartS asc""",
    "agents": """OTelSpans
| where TimeGenerated {TimeRange}
| extend Agent = tostring(Attributes["gen_ai.agent.name"])
| where isnotempty(Agent) and Name startswith "invoke_agent"
| summarize Calls = count(), P50ms = round(percentile(DurationMs, 50)),
    P95ms = round(percentile(DurationMs, 95)) by Agent
| order by P95ms desc""",
    "models": """OTelSpans
| where TimeGenerated {TimeRange}
| where Name startswith "chat "
| extend Model = tostring(Attributes["gen_ai.request.model"]), Service = ServiceName
| summarize Calls = count(), P50ms = round(percentile(DurationMs, 50)),
    P95ms = round(percentile(DurationMs, 95)),
    InputTokens = sum(toint(Attributes["gen_ai.usage.input_tokens"])),
    OutputTokens = sum(toint(Attributes["gen_ai.usage.output_tokens"])) by Model, Service
| order by Calls desc""",
    "router_latency": """DriftlineDecisions_CL
| where TimeGenerated {TimeRange}
| summarize Decisions = count(), P50ms = round(percentile(LatencyMs, 50)),
    P95ms = round(percentile(LatencyMs, 95)), P99ms = round(percentile(LatencyMs, 99)),
    Escalated = countif(Escalated) by DecisionType, Client
| order by DecisionType asc""",
    "confidence": """DriftlineDecisions_CL
| where TimeGenerated {TimeRange}
| mv-apply kv = parse_json(tostring(Scores)) on (
    extend v = todouble(kv[tostring(bag_keys(kv)[0])]) | summarize Top = max(v))
| summarize Decisions = count() by Confidence = bin(Top, 0.1), DecisionType
| order by Confidence asc""",
    "cost": f"""let prices = datatable(Model: string, InPerM: real, OutPerM: real) [{PRICE_TABLE}];
let incidentTraces = OTelSpans
| where TimeGenerated {{TimeRange}}
| extend Incident = tostring(Attributes["driftline.incident_id"])
| where isnotempty(Incident)
| distinct TraceId, Incident;
let tokens = OTelSpans
| where TimeGenerated {{TimeRange}} and Name startswith "chat "
| extend Model = tostring(Attributes["gen_ai.request.model"]),
    In = toint(Attributes["gen_ai.usage.input_tokens"]),
    Out = toint(Attributes["gen_ai.usage.output_tokens"])
| join kind=inner incidentTraces on TraceId
| join kind=leftouter prices on Model
| summarize TokensUsd = sum((In * InPerM + Out * OutPerM) / 1000000.0) by Incident;
let decisions = DriftlineDecisions_CL
| where TimeGenerated {{TimeRange}} and isnotempty(IncidentId)
| summarize DecisionsUsd = sum(CostUsd) by Incident = IncidentId;
tokens
| join kind=fullouter decisions on Incident
| project Incident = coalesce(Incident, Incident1), TokensUsd = round(coalesce(TokensUsd, 0.0), 5),
    DecisionsUsd = round(coalesce(DecisionsUsd, 0.0), 5)
| extend TotalUsd = TokensUsd + DecisionsUsd
| order by TotalUsd desc
| take 30""",
    "escalation": """DriftlineDecisions_CL
| where TimeGenerated {TimeRange}
| summarize EscalationRate = round(todouble(countif(Escalated)) / count(), 4)
    by bin(TimeGenerated, 1h), DecisionType
| render timechart""",
    "evals": """DriftlineEvals_CL
| where TimeGenerated {TimeRange}
| where Metric in ("pass_rate", "detection_recall", "routing_accuracy", "rca_top1_exact",
    "correct_action_rate", "fix_acceptance_rate") or Metric endswith ".D3.accuracy"
| project TimeGenerated, Series = strcat(Layer, " ", Metric), Value
| render timechart""",
    "evals_latest": """DriftlineEvals_CL
| where TimeGenerated {TimeRange}
| summarize arg_max(TimeGenerated, Value, RunId, Report, Source) by Layer, Metric
| order by Layer asc, Metric asc""",
}


def text(md: str) -> dict[str, Any]:
    return {"type": 1, "content": {"json": md}}


def query(
    title: str, kql: str, viz: str = "table", size: int = 0, cond: str | None = None, **extra: Any
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": 3,
        "content": {
            "version": "KqlItem/1.0",
            "query": kql,
            "size": size,
            "title": title,
            "timeContextFromParameter": "TimeRange",
            "queryType": 0,
            "resourceType": "microsoft.operationalinsights/workspaces",
            "crossComponentResources": ["{Workspace}"],
            "visualization": viz,
            **extra,
        },
        "name": title.lower().replace(" ", "-"),
    }
    if cond:
        item["conditionalVisibility"] = {
            "parameterName": cond,
            "comparison": "isNotEqualTo",
            "value": "",
        }
    return item


def bar(column: str, palette: str = "blue") -> dict[str, Any]:
    return {"columnMatch": column, "formatter": 4, "formatOptions": {"palette": palette}}


def workbook() -> dict[str, Any]:
    params = {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [
                {
                    "id": "ws",
                    "version": "KqlParameterItem/1.0",
                    "name": "Workspace",
                    "type": 5,
                    "isRequired": True,
                    "typeSettings": {
                        "resourceTypeFilter": {"microsoft.operationalinsights/workspaces": True}
                    },
                    "value": "{WorkspaceId}",
                    "isHiddenWhenLocked": True,
                },
                {
                    "id": "tr",
                    "version": "KqlParameterItem/1.0",
                    "name": "TimeRange",
                    "type": 4,
                    "value": {"durationMs": 604800000},
                    "typeSettings": {
                        "selectableValues": [
                            {"durationMs": 3600000},
                            {"durationMs": 86400000},
                            {"durationMs": 259200000},
                            {"durationMs": 604800000},
                            {"durationMs": 2592000000},
                        ]
                    },
                },
                {
                    "id": "inc",
                    "version": "KqlParameterItem/1.0",
                    "name": "Incident",
                    "type": 2,
                    "query": Q["incidents"],
                    "queryType": 0,
                    "resourceType": "microsoft.operationalinsights/workspaces",
                    "crossComponentResources": ["{Workspace}"],
                    "timeContextFromParameter": "TimeRange",
                    "typeSettings": {"showDefault": False},
                },
            ],
            "style": "pills",
            "queryType": 0,
            "resourceType": "microsoft.operationalinsights/workspaces",
        },
        "name": "parameters",
    }
    items = [
        text(
            "# Driftline\nIncidents from detection to fix, the router's decisions, the agents' "
            "latency and cost, and eval trends. Spans come from every service through "
            "otel-collector (`OTelSpans`); router decisions from `DriftlineDecisions_CL`; eval "
            "summaries from `DriftlineEvals_CL`."
        ),
        params,
        text(
            "## Incident timeline\nEach workflow step per incident, from the detector to "
            "the PR or the approval."
        ),
        query("Incident timeline", Q["timeline"], gridSettings={"formatters": [bar("Seconds")]}),
        text(
            "## Trace waterfall\nPick an incident above: every span of its traces, in start order."
        ),
        query(
            "Trace waterfall",
            Q["waterfall"],
            cond="Incident",
            gridSettings={"formatters": [bar("DurationS", "orange")], "rowLimit": 2000},
        ),
        text("## Latency by agent and model"),
        query("Latency by agent", Q["agents"], gridSettings={"formatters": [bar("P95ms")]}),
        query(
            "Latency and tokens by model", Q["models"], gridSettings={"formatters": [bar("P95ms")]}
        ),
        text("## Router decisions"),
        query(
            "Router decision latency",
            Q["router_latency"],
            gridSettings={"formatters": [bar("P95ms")]},
        ),
        query("Confidence distribution", Q["confidence"], viz="barchart"),
        query("Escalation rate", Q["escalation"], viz="timechart"),
        text(
            "## Cost per incident\nAgent tokens (priced from config/pricing.yaml) and "
            "router decisions."
        ),
        query(
            "Cost per incident",
            Q["cost"],
            viz="barchart",
            chartSettings={
                "seriesLabelSettings": [],
                "xAxis": "Incident",
                "yAxis": ["TokensUsd", "DecisionsUsd"],
                "group": None,
                "createOtherGroup": 0,
            },
        ),
        text(
            "## Eval trends\nLayer 2a, 2b and 3 summaries from local and nightly runs "
            "(`driftline-eval`)."
        ),
        query("Eval trends", Q["evals"], viz="timechart"),
        query("Latest eval results", Q["evals_latest"]),
    ]
    return {
        "version": "Notebook/1.0",
        "items": items,
        "isLocked": False,
        "fallbackResourceIds": ["{WorkspaceId}"],
        "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
    }


def main() -> None:
    out = ROOT / "infra" / "workbooks" / "driftline.json"
    out.write_text(json.dumps(workbook(), indent=2) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
