"""Layer 2a: router benchmark (spec 7.2).

Runs every DecisionClient over the routing dataset (D1-D4), the graded fix set (D5) and the
paraphrase perturbations (flip rate). Each decision is appended to a per-client results
file, so runs resume after interruption and the report recomputes offline.

Latency is reported twice: `model` is the client's own measurement of the model call
(what the router service records), `client` is wall time around decide() in this process
including prompt building and parsing.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from driftline_common.config import load_router_config
from driftline_common.decision import CATALOG, DecisionClient

EVALS = Path(__file__).resolve().parents[1]
DATASETS = EVALS / "datasets"
RESULTS = EVALS / "fixtures" / "router"
REPORTS = EVALS / "reports"
CONCURRENCY = {"gpt-5-mini": 12, "gpt-4.1-mini": 4, "gpt-oss-120b": 4, "rules": 64}


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def tasks(sample: int | None = None) -> list[dict[str, Any]]:
    """Every (decision type, case) pair with its label and context."""
    cases = _read(DATASETS / "routing.jsonl")
    perturbed = _read(DATASETS / "routing_perturbations.jsonl")
    fixes = _read(DATASETS / "fixes.jsonl")
    if sample:
        cases = cases[:sample] + [c for c in cases if not c["labels"]["is_real"]][: sample // 5]
        keep = {c["id"] for c in cases}
        perturbed = [p for p in perturbed if p["source_id"] in keep]
        fixes = fixes[: max(10, sample // 6)]
    out: list[dict[str, Any]] = []

    def ctx(c: dict[str, Any], dtype: str) -> dict[str, Any]:
        # Live agent health is D3 context only (spec 4.2).
        ev = {k: v for k, v in c["evidence"].items() if dtype == "D3" or k != "agent_health"}
        return {"summary": c["summary"], **ev}

    for c in cases:
        lab = c["labels"]
        out.append(
            {
                "key": f"D1:{c['id']}",
                "type": "D1",
                "id": c["id"],
                "context": ctx(c, "D1"),
                "label": "real" if lab["is_real"] else "noise",
            }
        )
        if not lab["is_real"]:
            continue
        out.append(
            {
                "key": f"D2:{c['id']}",
                "type": "D2",
                "id": c["id"],
                "context": ctx(c, "D2"),
                "label": lab["severity"],
            }
        )
        out.append(
            {
                "key": f"D3:{c['id']}",
                "type": "D3",
                "id": c["id"],
                "context": ctx(c, "D3"),
                "label": lab["specialist"],
                "secondary": lab["specialist_secondary"],
            }
        )
        out.append(
            {
                "key": f"D4:{c['id']}",
                "type": "D4",
                "id": c["id"],
                "context": ctx(c, "D4"),
                "label": lab["action"],
            }
        )
    for p in perturbed:
        lab = p["labels"]
        for t, label in (("D2", lab["severity"]), ("D3", lab["specialist"]), ("D4", lab["action"])):
            out.append(
                {
                    "key": f"{t}:{p['id']}",
                    "type": t,
                    "id": p["id"],
                    "source": p["source_id"],
                    "context": ctx(p, t),
                    "label": label,
                    "perturbation": p["kind"],
                }
            )
    for f in fixes:
        out.append(
            {
                "key": f"D5:{f['id']}",
                "type": "D5",
                "id": f["id"],
                "context": {"patch_diff": f["patch_diff"], "eval": f["eval"]},
                "label": f["labels"]["verdict"],
            }
        )
    return out


async def run_client(name: str, client: DecisionClient, todo: list[dict[str, Any]]) -> int:
    path = RESULTS / f"results-{name}.jsonl"
    # Errored decisions (e.g. throttling) are retried on the next run; the newest record
    # for a key wins when scoring.
    done = {r["key"] for r in _read(path) if not r.get("error")} if path.exists() else set()
    pending = [t for t in todo if t["key"] not in done]
    sem = asyncio.Semaphore(CONCURRENCY.get(name, 8))
    lock = asyncio.Lock()
    path.parent.mkdir(parents=True, exist_ok=True)

    async def one(t: dict[str, Any]) -> None:
        spec = CATALOG[t["type"]]
        async with sem:
            t0 = time.perf_counter()
            try:
                d = await client.decide(
                    spec.question,
                    list(spec.options),
                    t["context"],
                    spec.rubric,
                    question_id=t["id"],
                    decision_type=t["type"],
                )
                rec = {
                    "key": t["key"],
                    "chosen": d.chosen,
                    "scores": d.scores,
                    "model_ms": d.latency_ms,
                    "input_tokens": d.input_tokens,
                    "output_tokens": d.output_tokens,
                    "cost_usd": d.cost_usd,
                    "invalid": d.rationale == "invalid_output",
                    "error": None,
                }
            except Exception as e:
                rec = {"key": t["key"], "error": f"{type(e).__name__}: {str(e)[:200]}"}
            rec["client_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        async with lock:
            with path.open("a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")

    await asyncio.gather(*(one(t) for t in pending))
    return len(pending)


# ---------- metrics ----------


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))], 1)


def macro_f1(pairs: list[tuple[str, str]], labels: Iterable[str]) -> float:
    f1s = []
    for lab in labels:
        tp = sum(1 for y, p in pairs if y == lab and p == lab)
        fp = sum(1 for y, p in pairs if y != lab and p == lab)
        fn = sum(1 for y, p in pairs if y == lab and p != lab)
        if tp + fp + fn == 0:
            continue
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return round(statistics.fmean(f1s), 4) if f1s else 0.0


def calibration(
    conf_correct: list[tuple[float, bool]], bins: int = 10
) -> tuple[float, list[dict[str, float]]]:
    """Expected calibration error with equal-width bins, plus reliability diagram points."""
    n = len(conf_correct)
    ece = 0.0
    diagram = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(c, ok) for c, ok in conf_correct if (lo <= c < hi) or (b == bins - 1 and c == 1)]
        if not sel:
            diagram.append(
                {"bin": b, "lo": lo, "hi": hi, "count": 0, "confidence": 0.0, "accuracy": 0.0}
            )
            continue
        conf = statistics.fmean(c for c, _ in sel)
        acc = statistics.fmean(1.0 if ok else 0.0 for _, ok in sel)
        ece += len(sel) / n * abs(acc - conf)
        diagram.append(
            {
                "bin": b,
                "lo": lo,
                "hi": hi,
                "count": len(sel),
                "confidence": round(conf, 4),
                "accuracy": round(acc, 4),
            }
        )
    return round(ece, 4), diagram


def score_client(name: str, todo: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    results = {r["key"]: r for r in _read(RESULTS / f"results-{name}.jsonl")}
    by_type: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for t in todo:
        r = results.get(t["key"])
        if r is not None:
            by_type[t["type"]].append((t, r))
    out: dict[str, Any] = {}
    for dtype in sorted(by_type):
        rows = by_type[dtype]
        originals = [(t, r) for t, r in rows if "source" not in t]
        ok = [(t, r) for t, r in originals if not r.get("error")]
        errors = len(originals) - len(ok)
        strict = [(t["label"], r["chosen"]) for t, r in ok]
        acc = statistics.fmean(1.0 if y == p else 0.0 for y, p in strict) if strict else 0.0
        lenient = (
            statistics.fmean(
                1.0 if r["chosen"] in (t["label"], t.get("secondary")) else 0.0 for t, r in ok
            )
            if ok
            else 0.0
        )
        conf = [(max(r["scores"].values()), r["chosen"] == t["label"]) for t, r in ok]
        ece, diagram = calibration(conf)
        # robustness: flips vs the decision on the unperturbed source case
        chosen_by_id = {t["id"]: r["chosen"] for t, r in ok}
        flips: dict[str, list[float]] = defaultdict(list)
        for t, r in rows:
            if "source" in t and not r.get("error") and t["source"] in chosen_by_id:
                flips[t["source"]].append(1.0 if r["chosen"] != chosen_by_id[t["source"]] else 0.0)
        flip_rate = statistics.fmean(statistics.fmean(v) for v in flips.values()) if flips else None
        all_ok = [r for _, r in rows if not r.get("error")]
        esc = [c < threshold for c, _ in conf]
        kept = [ok_ for (c, ok_) in conf if c >= threshold]
        out[dtype] = {
            "n": len(originals),
            "errors": errors,
            "accuracy": round(acc, 4),
            "accuracy_lenient": round(lenient, 4) if dtype == "D3" else None,
            "macro_f1": macro_f1(strict, CATALOG[dtype].options),
            "ece": ece,
            "reliability": diagram,
            "flip_rate": None if flip_rate is None else round(flip_rate, 4),
            "latency_model_ms": {
                "p50": _pct([r["model_ms"] for r in all_ok], 0.5),
                "p95": _pct([r["model_ms"] for r in all_ok], 0.95),
                "p99": _pct([r["model_ms"] for r in all_ok], 0.99),
            },
            "latency_client_ms": {
                "p50": _pct([r["client_ms"] for r in all_ok], 0.5),
                "p95": _pct([r["client_ms"] for r in all_ok], 0.95),
                "p99": _pct([r["client_ms"] for r in all_ok], 0.99),
            },
            "cost_per_1k_usd": round(1000 * statistics.fmean(r["cost_usd"] for r in all_ok), 4)
            if all_ok
            else 0.0,
            "invalid_output_rate": round(
                statistics.fmean(1.0 if r.get("invalid") else 0.0 for r in all_ok), 4
            )
            if all_ok
            else 0.0,
            "escalation_rate": round(statistics.fmean(1.0 if e else 0.0 for e in esc), 4)
            if esc
            else 0.0,
            "accuracy_not_escalated": round(statistics.fmean(1.0 if k else 0.0 for k in kept), 4)
            if kept
            else None,
        }
    return out


def report(clients: list[str], sample: int | None, threshold: float) -> dict[str, Any]:
    todo = tasks(sample)
    data = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "escalation_threshold": threshold,
        "sample": sample,
        "clients": {c: score_client(c, todo, threshold) for c in clients},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    base = REPORTS / f"{data['generated']}-router"
    base.with_suffix(".json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    base.with_suffix(".md").write_text(render_markdown(data))
    return data


def render_markdown(data: dict[str, Any]) -> str:
    clients = list(data["clients"])
    types = sorted({t for c in clients for t in data["clients"][c]})
    lines = [
        "# Router benchmark (layer 2a)",
        "",
        f"Generated {data['generated']}. Escalation threshold {data['escalation_threshold']}."
        + (f" Sample: {data['sample']}." if data["sample"] else " Full dataset."),
        "",
        "Accuracy is strict (primary label). D3 also shows lenient accuracy (primary or "
        "secondary specialist on ambiguous cases). ECE uses 10 equal-width bins on the top "
        "score. Flip rate is the share of the 8 perturbations whose decision differs from the "
        "unperturbed case. Latency is the model call (router-side) / wall time in the client.",
        "",
    ]
    for t in types:
        lines += [
            f"## {t}: {CATALOG[t].question}",
            "",
            "| Client | n | Acc | Lenient | Macro F1 | ECE | Flip | p50 / p95 / p99 ms "
            "(model) | p95 ms (client) | $/1k | Esc. rate | Acc (not esc.) | Invalid |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for c in clients:
            m = data["clients"][c].get(t)
            if not m:
                continue
            lat = m["latency_model_ms"]

            def f3(v: float | None) -> str:
                return "" if v is None else f"{v:.3f}"

            lines.append(
                f"| {c} | {m['n']} | {m['accuracy']:.3f} | {f3(m['accuracy_lenient'])} | "
                f"{m['macro_f1']:.3f} | {m['ece']:.3f} | {f3(m['flip_rate'])} | "
                f"{lat['p50']:.0f} / {lat['p95']:.0f} / {lat['p99']:.0f} | "
                f"{m['latency_client_ms']['p95']:.0f} | {m['cost_per_1k_usd']:.3f} | "
                f"{m['escalation_rate']:.3f} | {f3(m['accuracy_not_escalated'])} | "
                f"{m['invalid_output_rate']:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)


async def run(clients: list[str] | None, sample: int | None) -> dict[str, Any]:
    from driftline_common.foundry import make_openai_client
    from driftline_common.pricing import Pricing
    from router.clients import build_clients

    cfg = load_router_config()
    openai = make_openai_client(max_retries=8)
    built = build_clients(cfg, openai, Pricing.load())
    names = clients or list(built)
    todo = tasks(sample)
    try:
        counts = await asyncio.gather(*(run_client(n, built[n], todo) for n in names))
    finally:
        await openai.close()
    print(dict(zip(names, counts, strict=True)))
    return report(names, sample, cfg.escalation_threshold)
