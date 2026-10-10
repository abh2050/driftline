"""Shapes of incident data for the console and MCP clients. Pure functions, no I/O."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    """One row of the live incident feed."""
    wf = doc.get("workflow") or {}
    pending = doc.get("pending") or {}
    return {
        "id": doc["id"],
        "status": doc.get("status"),
        "severity": doc.get("severity"),
        "stream": doc.get("stream"),
        "breached_metrics": doc.get("breached_metrics", []),
        "opened_at": doc.get("opened_at"),
        "detected_at": doc.get("detected_at"),
        "closed_at": doc.get("closed_at"),
        "specialist": wf.get("specialist"),
        "action": doc.get("action") or wf.get("action"),
        "pr_url": doc.get("pr_url"),
        "awaiting": pending.get("kind"),
        "fault_label": doc.get("fault_label"),
        "updated": doc.get("_ts"),
    }


def trace_url(doc: dict[str, Any]) -> str | None:
    """Log Analytics query for every span of the incident's trace."""
    parent = doc.get("traceparent") or ""
    parts = parent.split("-")
    appi = os.environ.get("APPINSIGHTS_RESOURCE_ID")
    tenant = os.environ.get("AZURE_TENANT_ID", "")
    if len(parts) != 4 or not appi:
        return None
    query = quote(f"union OTelSpans | where TraceId == '{parts[1]}'")
    return f"https://portal.azure.com/#@{tenant}/resource{appi}/logs?query={query}"


def decision(d: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, float] = d.get("scores", {})
    ranked = sorted(scores.values(), reverse=True)
    return {
        "chosen": d.get("chosen"),
        "scores": scores,
        "margin": d.get("margin", round(ranked[0] - ranked[1], 4) if len(ranked) > 1 else None),
        "escalated": d.get("escalated", False),
        "rationale": d.get("rationale"),
        "client": d.get("client"),
        "cost_usd": d.get("cost_usd"),
        "latency_ms": d.get("latency_ms"),
    }


def detail(doc: dict[str, Any]) -> dict[str, Any]:
    """Everything the incident page shows (spec 5.2b)."""
    wf = doc.get("workflow") or {}
    out = _clean(doc)
    out.pop("workflow", None)
    out.update(
        {
            "summary": summary(doc),
            "decisions": {k: decision(v) for k, v in (wf.get("decisions") or {}).items()},
            "specialists": wf.get("specialists", []),
            "specialist": wf.get("specialist"),
            "rca": wf.get("rca"),
            "rcas": wf.get("rcas", {}),
            "action": wf.get("action"),
            "escalations": wf.get("escalations", []),
            "validation": doc.get("validation") or wf.get("validation"),
            "costs": wf.get("costs", {}),
            "notes": wf.get("notes", []),
            "resolution": doc.get("resolution") or wf.get("resolution"),
            "trace_url": trace_url(doc),
        }
    )
    return out


def approval(doc: dict[str, Any]) -> dict[str, Any] | None:
    """An incident waiting for a person, or None."""
    pending = doc.get("pending")
    if doc.get("status") != "awaiting_human" or not pending:
        return None
    wf = doc.get("workflow") or {}
    return {
        "incident_id": doc["id"],
        "kind": pending.get("kind"),
        "summary": pending.get("summary"),
        "runbook": pending.get("runbook", []),
        "reasons": pending.get("reasons", []),
        "severity": doc.get("severity"),
        "specialist": wf.get("specialist"),
        "detected_at": doc.get("detected_at"),
    }
