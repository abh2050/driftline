"""Build docs/site/index.html (the GitHub Pages site) from docs/site/template.html, the latest
eval reports and one real incident (apps/console/tests/fixtures/real.json, exported by
scripts/export_console_fixture.py).

uv run python -m scripts.build_site
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
SITE = ROOT / "docs" / "site"
INCIDENT = ROOT / "apps" / "console" / "tests" / "fixtures" / "real.json"
REPO = "https://github.com/abh2050/driftline"

SHOTS = [
    (
        "Incident",
        "incident.png",
        "/incidents/[id]",
        "An incident: routing scores with margins, the root cause with cited evidence, the "
        "sandbox evaluation, D5/D6 and the pull request.",
    ),
    (
        "Live feed",
        "incidents.png",
        "/incidents",
        "Every incident, live over server-sent events, with browser notifications for sev1, "
        "sev2 and escalations.",
    ),
    (
        "Approvals",
        "approvals.png",
        "/approvals",
        "Runbooks and escalations wait here; approving resumes the paused workflow from its "
        "checkpoint.",
    ),
    (
        "Explainer",
        "chat.png",
        "/chat",
        "Ask the explainer agent about any incident; it answers from the record.",
    ),
    (
        "Router",
        "router.png",
        "/router",
        "The router benchmark per decision type, with a reliability diagram per client.",
    ),
    (
        "Demo",
        "demo.png",
        "/demo",
        "Admin-only demo controls: replay any fault through the real pipeline, or inject it live.",
    ),
]


def latest(pattern: str) -> dict[str, Any]:
    path = sorted(REPORTS.glob(pattern))[-1]
    data: dict[str, Any] = json.loads(path.read_text())
    data["_file"] = path.name
    return data


def router_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for client in ("gpt-5-mini", "gpt-4.1-mini", "gpt-oss-120b", "rules"):
        types = report["clients"].get(client)
        if not types:
            continue
        d3, d4 = types["D3"], types["D4"]
        flip = d3.get("flip_rate")
        rows.append(
            {
                "client": client,
                "active": client == "gpt-5-mini",
                "d3": round(d3["accuracy"] * 100, 1),
                "d4": round(d4["accuracy"] * 100, 1),
                "ece": f"{d3['ece']:.3f}",
                "flip": "-" if flip is None else f"{flip * 100:.1f}%",
                "p95": f"{d3['latency_model_ms']['p95'] / 1000:.1f} s",
                "cost": f"${d3['cost_per_1k_usd']:.2f}",
            }
        )
    return rows


def incident() -> dict[str, Any]:
    d = json.loads(INCIDENT.read_text())["detail"]
    v = d["validation"]
    b, c = v["baseline"], v["candidate"]
    keys = [
        ("recall_at_k", "higher"),
        ("groundedness", "higher"),
        ("citation_rate", "higher"),
        ("answer_accuracy", "higher"),
        ("latency_p95_ms", "lower"),
    ]
    rows = []
    for k, better in keys:
        improved = c[k] > b[k] if better == "higher" else c[k] < b[k]
        rows.append([k, b[k], c[k], improved])
    rca = d["rca"]
    change = ", ".join(f"{x['key']}: {x['value']}" for x in rca["config_changes"])
    delta = next(iter(d["metric_deltas"].values()))
    return {
        "id": d["id"],
        "d1": d["d1"],
        "delta": {
            "baseline": round(delta["baseline"], 1),
            "current": round(delta["current"], 1),
            "delta_pct": delta["delta_pct"],
            "cusum": delta.get("cusum"),
        },
        "decisions": d["decisions"],
        "rca": {**rca, "change": change},
        "validation": {"questions": v["questions"], "rows": rows},
        "pr_url": d["pr_url"],
        "cost": d["costs"].get("tokens_usd", 0) + d["costs"].get("decisions_usd", 0),
    }


def build() -> str:
    system = latest("*-system.json")["summary"]
    safety = latest("*-layer3.json")
    router = latest("*-router.json")
    inc = incident()
    fp = system.get("false_positives") or {}
    data = {
        "system": {"ttd": system["time_to_detect_min_median"]},
        "incident": inc,
        "router": router_rows(router),
        "safety": [
            {
                "id": c["id"],
                "attempt": c["attempt"],
                "by": c["blocked_by"],
                "layers": c["blocking_layers"],
            }
            for c in safety["cases"]
        ],
        "shots": [{"title": t, "file": f, "path": p, "caption": c} for t, f, p, c in SHOTS],
    }
    s = safety["summary"]
    tokens = {
        "__REPO__": REPO,
        "__INC_ID__": inc["id"],
        "__PR_NUM__": inc["pr_url"].rstrip("/").split("/")[-1],
        "__TTD__": f"{system['time_to_detect_min_median']:g}",
        "__CUSUM__": f"{inc['delta']['cusum']:.0f}",
        "__TRIALS__": str(system["trials"]),
        "__FP_HOURS__": str(fp.get("hours", 48)),
        "__FP__": str(fp.get("incidents", 0)),
        "__DETECTION__": f"{system['detection_recall'] * 100:.0f}",
        "__ACTION__": f"{system['correct_action_rate'] * 100:.0f}",
        "__ROUTING__": f"{system['routing_accuracy'] * 100:.0f}",
        "__FIX__": f"{system['fix_acceptance_rate'] * 100:.0f}",
        "__COST__": f"{system['cost_per_incident_usd']:.2f}",
        "__WALL__": f"{system['wall_time_s_median'] / 60:.0f}",
        "__SEVERITY__": f"{system['severity_accuracy'] * 100:.0f}",
        "__SAFETY_CASES__": str(s["cases"]),
        "__SAFETY_PASSED__": str(round(s["pass_rate"] * s["cases"])),
        "__SHIELD__": f"{(s['content_safety_detection_rate'] or 0) * 100:.0f}",
        "__ROUTER_FILE__": router["_file"],
        # last: nothing is substituted inside the JSON payload
        "__DATA__": json.dumps(data, separators=(",", ":")).replace("</", "<\\/"),
    }
    out = (SITE / "template.html").read_text()
    for k, v in tokens.items():
        out = out.replace(k, v)
    return out


def main() -> None:
    (SITE / "index.html").write_text(build())
    print("wrote docs/site/index.html")


if __name__ == "__main__":
    main()
