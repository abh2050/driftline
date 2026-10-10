"""Layer 1 metric aggregation and the regression gate. Pure functions, no Azure calls."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


def _pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return float(s[idx])


def _mean(values: Sequence[float | None]) -> float:
    xs = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    return round(statistics.fmean(xs), 4) if xs else 0.0


def _scored(v: Any) -> bool:
    return v is not None and not math.isnan(float(v))


def aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """rows: one per question with retrieved_doc_ids, source_doc_ids, answer, citations,
    expected, latency_ms, cost_usd, error, and judge scores."""
    ok = [r for r in rows if not r.get("error")]
    recall, rr = [], []
    for r in ok:
        src = set(r["source_doc_ids"])
        got = r["retrieved_doc_ids"]
        recall.append(1.0 if src & set(got) else 0.0)
        rank = next((i + 1 for i, d in enumerate(got) if d in src), None)
        rr.append(1.0 / rank if rank else 0.0)
    lat = [r["latency_ms"] for r in ok]
    return {
        "questions": float(len(rows)),
        "error_rate": round(1 - len(ok) / len(rows), 4) if rows else 0.0,
        "recall_at_k": _mean(recall),
        "mrr": _mean(rr),
        "groundedness": _mean([r.get("groundedness") for r in ok]),
        "relevance": _mean([r.get("relevance") for r in ok]),
        "response_completeness": _mean([r.get("response_completeness") for r in ok]),
        "judged": float(sum(1 for r in ok if _scored(r.get("groundedness")))),
        "citation_rate": _mean([1.0 if r["citations"] else 0.0 for r in ok]),
        "answer_accuracy": _mean(
            [1.0 if r["expected"].lower() in r["answer"].lower() else 0.0 for r in ok]
        ),
        "latency_p50_ms": round(_pct(lat, 0.50), 1),
        "latency_p95_ms": round(_pct(lat, 0.95), 1),
        "cost_per_query_usd": round(statistics.fmean([r["cost_usd"] for r in ok]), 6)
        if ok
        else 0.0,
    }


@dataclass
class GateResult:
    passed: bool
    recovered: bool
    regressions: dict[str, float]
    details: dict[str, dict[str, float]]


def relative_change(direction: str, baseline: float, value: float) -> float:
    """Positive = worse than baseline, as a fraction of baseline."""
    if baseline == 0:
        return 0.0
    delta = (baseline - value) / abs(baseline)
    return delta if direction == "higher" else -delta


JUDGE_METRICS = ("groundedness", "relevance", "response_completeness")


def paired_stderr(
    base_rows: Sequence[dict[str, Any]], cand_rows: Sequence[dict[str, Any]], metric: str
) -> float:
    """Standard error of the mean per-question difference (same questions in both runs)."""
    base = {r["id"]: r.get(metric) for r in base_rows}
    diffs = [
        float(c[metric]) - float(base[c["id"]])  # type: ignore[arg-type]
        for c in cand_rows
        if _scored(c.get(metric)) and _scored(base.get(c["id"]))
    ]
    if len(diffs) < 3:
        return 0.0
    return statistics.stdev(diffs) / math.sqrt(len(diffs))


def gate(
    baseline: dict[str, float],
    candidate: dict[str, float],
    degraded_metric: str,
    gates: dict[str, Any],
    noise: dict[str, float] | None = None,
) -> GateResult:
    """noise: per-metric standard error of the paired difference. A regression must exceed
    both the configured threshold and two standard errors, so LLM-judge noise on a small
    sample is not mistaken for a regression."""
    specs = gates["metrics"]
    tol = float(gates["recovery_tolerance"])
    details: dict[str, dict[str, float]] = {}
    regressions: dict[str, float] = {}
    for name, spec in specs.items():
        if name not in baseline or name not in candidate:
            continue
        worse = relative_change(spec["direction"], baseline[name], candidate[name])
        details[name] = {
            "baseline": baseline[name],
            "candidate": candidate[name],
            "regression": round(worse, 4),
        }
        se = (noise or {}).get(name, 0.0)
        noise_rel = 2 * se / abs(baseline[name]) if baseline[name] else 0.0
        details[name]["noise_rel"] = round(noise_rel, 4)
        if name != degraded_metric and worse > max(float(spec["max_regression"]), noise_rel):
            regressions[name] = round(worse, 4)
    spec = specs[degraded_metric]
    recovered = (
        relative_change(spec["direction"], baseline[degraded_metric], candidate[degraded_metric])
        <= tol
    )
    return GateResult(recovered and not regressions, recovered, regressions, details)
