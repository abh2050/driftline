"""Routing dataset (spec 3.5) for the layer 2a router benchmark.

Writes to evals/datasets/:
  routing.jsonl                1,200 real incident cases (D2 severity, D3 specialist, D4 action),
                               20% ambiguous with a secondary specialist, plus 240 noise
                               cases (D1 real vs noise). Field `labels.is_real` separates them.
  routing_perturbations.jsonl  8 perturbations for 200 real cases (4 LLM paraphrases of the
                               summary + 4 structural rewrites), same labels.
  fixes.jsonl                  200 graded fix cases for D5 (accept / reject).

Usage: uv run python -m synth.routing [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
from dataclasses import dataclass, field
from typing import Any

from synth.common import (
    DATASETS_DIR,
    LLM,
    CachedLLM,
    aclose,
    complete_json,
    load_synth_config,
    rng,
    sha256_file,
    write_jsonl,
    write_manifest,
)

VERSION = "1.0.0"
N_REAL = 1200
N_NOISE = 240
AMBIGUOUS_SHARE = 0.20
N_PERTURBED = 200
N_FIXES = 200
SPECIALISTS = ["retrieval", "prompt", "index", "performance"]

BASE = {
    "groundedness": 4.4,
    "top_score": 0.82,
    "citation_rate": 0.93,
    "hit_count": 7.9,
    "p95_latency_ms": 2750.0,
    "error_rate": 0.004,
    "tokens_in": 2300.0,
}
HIGHER_IS_BETTER = {"groundedness", "top_score", "citation_rate", "hit_count"}


@dataclass(frozen=True)
class Cause:
    id: str
    specialist: str
    action: str
    effects: dict[str, float]  # metric -> relative change at nominal strength
    diff: list[tuple[str, Any, Any]]
    logs: list[str]
    fault: str | None = None
    files: list[str] = field(default_factory=list)


C = Cause
CAUSES: list[Cause] = [
    C(
        "chunk_size_increase",
        "retrieval",
        "pr",
        {"top_score": -0.11, "groundedness": -0.16, "tokens_in": 2.3, "p95_latency_ms": 0.2},
        [("chunk_size", 512, 2048)],
        [
            "retrieval: 8 hits, mean chunk length 1987 tokens",
            "prompt truncated to fit context window (dropped 3 chunks)",
        ],
        "F1",
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "chunk_size_decrease",
        "retrieval",
        "pr",
        {"top_score": -0.06, "groundedness": -0.12, "citation_rate": -0.1},
        [("chunk_size", 512, 128)],
        ["retrieval: chunks average 121 tokens; answers missing table rows"],
        None,
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "embedding_swap_no_reindex",
        "retrieval",
        "pr",
        {"top_score": -0.7, "groundedness": -0.5, "citation_rate": -0.4},
        [("embedding_deployment", "text-embedding-3-small", "text-embedding-3-large")],
        [
            "search: vector dimension 3072 queried against field with 1536 dims, fell back to "
            "keyword",
            "retrieval: top score 0.21 below floor 0.5",
        ],
        "F2",
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "top_k_reduced",
        "retrieval",
        "pr",
        {"hit_count": -0.75, "groundedness": -0.25, "citation_rate": -0.2, "tokens_in": -0.7},
        [("top_k", 8, 2)],
        ["retrieval: returned 2 hits (top_k=2)"],
        "F3",
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "chunk_overlap_zero",
        "retrieval",
        "pr",
        {"groundedness": -0.08, "top_score": -0.04},
        [("chunk_overlap", 64, 0)],
        ["chunker: overlap=0, 14% of answers span chunk boundary"],
        None,
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "prompt_citation_removed",
        "prompt",
        "pr",
        {"citation_rate": -0.85, "groundedness": -0.08},
        [("prompt_version", "v3", "v4-nocite")],
        ["answer: 0 citations in response", "prompt v4-nocite loaded"],
        "F4",
        ["apps/patient-rag/prompts/v4-nocite.md", "apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "prompt_rewrite_ungrounded",
        "prompt",
        "pr",
        {"groundedness": -0.3, "citation_rate": -0.15},
        [("prompt_version", "v3", "v5")],
        [
            "judge: answer contains claims not in context",
            "prompt v5 loaded (system prompt rewritten)",
        ],
        None,
        ["apps/patient-rag/prompts/v5.md", "apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "citation_format_changed",
        "prompt",
        "pr",
        {"citation_rate": -0.5},
        [("prompt_version", "v3", "v3.1")],
        ["citation parser: 0 matches for pattern [doc:ID]"],
        None,
        ["apps/patient-rag/prompts/v3.1.md", "apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "indexer_disabled_stale",
        "index",
        "runbook",
        {"groundedness": -0.05},
        [],
        [
            "indexer cfw-docs-indexer: status=disabled since 3h",
            "search: 30 documents older than source revision",
        ],
        "F5",
    ),
    C(
        "indexer_failing_runtime",
        "index",
        "runbook",
        {"groundedness": -0.07, "hit_count": -0.05},
        [],
        [
            "indexer cfw-docs-indexer: last run failed (quota exceeded on skillset)",
            "search: document count 271, source count 300",
        ],
    ),
    C(
        "index_schema_field_dropped",
        "index",
        "pr",
        {"top_score": -0.15, "citation_rate": -0.3},
        [("index_schema.fields.doc_id.retrievable", True, False)],
        ["citation parser: doc_id missing from search results"],
        None,
        ["apps/patient-rag/config/index_schema.json"],
    ),
    C(
        "reranker_slow_tier",
        "performance",
        "pr",
        {"p95_latency_ms": 0.6, "top_score": 0.02},
        [
            ("reranker_enabled", False, True),
            ("reranker_deployment", "Cohere-rerank-v4.0-fast", "Cohere-rerank-v4.0-pro"),
        ],
        ["rerank: p95 1410 ms (Cohere-rerank-v4.0-pro)"],
        "F6",
        ["apps/patient-rag/config/rag.yaml"],
    ),
    C(
        "chat_throttled",
        "performance",
        "escalate",
        {"error_rate": 40.0, "p95_latency_ms": 0.6},
        [],
        ["chat: 429 Too Many Requests from gateway (rate limit)", "retry: attempt 3/3 failed"],
        "F7",
    ),
    C(
        "search_capacity",
        "performance",
        "escalate",
        {"p95_latency_ms": 0.9, "error_rate": 6.0},
        [],
        ["search: 503 Service Unavailable (throttled, replicas=1)"],
    ),
    C(
        "min_replicas_zero",
        "performance",
        "pr",
        {"p95_latency_ms": 0.8},
        [("scale.min_replicas", 1, 0)],
        ["container: cold start 6.2 s"],
        None,
        ["apps/patient-rag/config/rag.yaml"],
    ),
]
CAUSE_BY_ID = {c.id: c for c in CAUSES}
NOISE_LOGS = [
    "healthcheck ok",
    "gc pause 120 ms",
    "autoscaler: replicas 2 -> 3",
    "token refresh ok",
    "search: warm cache miss",
]


def severity(m: dict[str, dict[str, float]]) -> str:
    def drop(k: str) -> float:
        return -m[k]["delta_pct"] / 100 if k in m else 0.0

    def rise(k: str) -> float:
        return m[k]["delta_pct"] / 100 if k in m else 0.0

    err = m["error_rate"]["current"] if "error_rate" in m else BASE["error_rate"]
    if err >= 0.10 or drop("groundedness") >= 0.40 or drop("top_score") >= 0.50:
        return "sev1"
    if (
        err >= 0.03
        or drop("groundedness") >= 0.15
        or drop("citation_rate") >= 0.50
        or rise("p95_latency_ms") >= 1.0
    ):
        return "sev2"
    if any(drop(k) >= 0.05 for k in HIGHER_IS_BETTER) or rise("p95_latency_ms") >= 0.4:
        return "sev3"
    return "sev4"


def metric_deltas(
    r: random.Random, effects: dict[str, float], strength: float
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for k, rel in effects.items():
        base = BASE[k] * r.uniform(0.95, 1.05)
        change = rel * strength * r.uniform(0.85, 1.15)
        cur = base * (1 + change)
        if k in HIGHER_IS_BETTER:
            cur = max(cur, 0.0)
        if k in ("citation_rate", "top_score"):
            cur = min(cur, 1.0)
        out[k] = {
            "baseline": round(base, 4),
            "current": round(cur, 4),
            "delta_pct": round((cur - base) / base * 100, 1),
        }
    return dict(sorted(out.items()))


def agent_health(r: random.Random) -> dict[str, dict[str, float]]:
    health = {}
    degraded = r.choice(SPECIALISTS) if r.random() < 0.25 else None
    for s in SPECIALISTS:
        bad = s == degraded
        health[s] = {
            "p95_latency_ms": round(r.uniform(8000, 20000) * (3 if bad else 1)),
            "error_rate": round(r.uniform(0, 0.02) + (0.15 if bad else 0), 3),
            "queue_depth": r.randrange(0, 3) + (12 if bad else 0),
        }
    return health


def summary(case: dict[str, Any]) -> str:
    ev = case["evidence"]
    parts = [
        f"{k} {v['baseline']} -> {v['current']} ({v['delta_pct']:+.1f}%)"
        for k, v in ev["metric_deltas"].items()
    ]
    diff = "; ".join(f"{d['key']}: {d['from']} -> {d['to']}" for d in ev["config_diff"]) or "none"
    return (
        f"Breach sustained for {ev['window_count']} one-minute windows. Metrics: "
        f"{', '.join(parts)}. Config changes since baseline: {diff}. Recent deploys: "
        f"{len(ev['recent_deploys'])}. Logs: {' | '.join(ev['log_snippets'])}"
    )


def _commit(r: random.Random) -> str:
    return format(r.getrandbits(28), "07x")


def real_case(r: random.Random, idx: int, ambiguous: bool) -> dict[str, Any]:
    primary = r.choice(CAUSES)
    strength = r.choice([0.25, 0.5, 0.8, 1.0, 1.0, 1.3])
    effects = dict(primary.effects)
    diff = [{"key": k, "from": a, "to": b} for k, a, b in primary.diff]
    logs = list(primary.logs)
    files = list(primary.files)
    secondary = None
    if ambiguous:
        others = [c for c in CAUSES if c.specialist != primary.specialist]
        sec = r.choice(others)
        secondary = sec.specialist
        for k, v in sec.effects.items():  # weaker secondary signal
            effects[k] = effects.get(k, 0) + v * 0.4
        diff += [{"key": k, "from": a, "to": b} for k, a, b in sec.diff]
        logs += sec.logs[:1]
        files += sec.files
    deltas = metric_deltas(r, effects, strength)
    deploys = []
    if diff or r.random() < 0.5:
        deploys.append(
            {
                "commit": _commit(r),
                "minutes_ago": r.randrange(5, 240),
                "files": sorted(set(files)) or ["apps/patient-rag/src/app.py"],
            }
        )
    if r.random() < 0.3:
        deploys.append(
            {
                "commit": _commit(r),
                "minutes_ago": r.randrange(300, 2000),
                "files": ["apps/console/app/page.tsx"],
            }
        )
    r.shuffle(logs)
    logs.append(r.choice(NOISE_LOGS))
    case: dict[str, Any] = {
        "id": f"R-{idx:04d}",
        "cause": primary.id,
        "fault": primary.fault,
        "evidence": {
            "metric_deltas": deltas,
            "window_count": r.randrange(3, 30),
            "config_diff": diff,
            "recent_deploys": deploys,
            "log_snippets": logs,
            "agent_health": agent_health(r),
        },
    }
    sev = severity(deltas)
    action = primary.action
    case["labels"] = {
        "is_real": True,
        "severity": sev,
        "specialist": primary.specialist,
        "specialist_secondary": secondary,
        "action": action,
        "ambiguous": ambiguous,
    }
    case["summary"] = summary(case)
    return case


def noise_case(r: random.Random, idx: int) -> dict[str, Any]:
    keys = r.sample(list(BASE), r.randrange(1, 3))
    deltas = metric_deltas(r, {k: r.uniform(-0.04, 0.04) or 0.01 for k in keys}, 1.0)
    if r.random() < 0.3:  # a sharp but transient latency spike
        deltas["p95_latency_ms"] = {
            "baseline": 2750.0,
            "current": round(r.uniform(4000, 6000)),
            "delta_pct": 0.0,
        }
        d = deltas["p95_latency_ms"]
        d["delta_pct"] = round((d["current"] - d["baseline"]) / d["baseline"] * 100, 1)
    deploys = (
        [
            {
                "commit": _commit(r),
                "minutes_ago": r.randrange(5, 600),
                "files": ["apps/patient-rag/src/app.py"],
            }
        ]
        if r.random() < 0.4
        else []
    )
    case: dict[str, Any] = {
        "id": f"N-{idx:04d}",
        "cause": "noise",
        "fault": None,
        "evidence": {
            "metric_deltas": dict(sorted(deltas.items())),
            "window_count": r.randrange(1, 3),
            "config_diff": [],
            "recent_deploys": deploys,
            "log_snippets": r.sample(NOISE_LOGS, 2),
            "agent_health": agent_health(r),
        },
        "labels": {
            "is_real": False,
            "severity": None,
            "specialist": None,
            "specialist_secondary": None,
            "action": None,
            "ambiguous": False,
        },
    }
    case["summary"] = summary(case)
    return case


SYNONYMS = {
    "groundedness": "grounding_score",
    "top_score": "retrieval_top_similarity",
    "citation_rate": "answers_with_citations",
    "hit_count": "retrieved_chunks",
    "p95_latency_ms": "latency_p95",
    "error_rate": "failed_request_ratio",
    "tokens_in": "prompt_tokens",
}


def structural_perturbations(r: random.Random, case: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    ev = case["evidence"]
    # 1. reorder every evidence collection
    e1 = copy.deepcopy(ev)
    items = list(e1["metric_deltas"].items())
    r.shuffle(items)
    e1["metric_deltas"] = dict(items)
    r.shuffle(e1["log_snippets"])
    r.shuffle(e1["config_diff"])
    out.append(("reorder", e1))
    # 2. latency in seconds instead of ms
    e2 = copy.deepcopy(ev)
    if "p95_latency_ms" in e2["metric_deltas"]:
        d = e2["metric_deltas"].pop("p95_latency_ms")
        e2["metric_deltas"]["p95_latency_s"] = {
            "baseline": round(d["baseline"] / 1000, 3),
            "current": round(d["current"] / 1000, 3),
            "delta_pct": d["delta_pct"],
        }
    out.append(("units", e2))
    # 3. synonym metric names
    e3 = copy.deepcopy(ev)
    e3["metric_deltas"] = {SYNONYMS.get(k, k): v for k, v in e3["metric_deltas"].items()}
    out.append(("synonyms", e3))
    # 4. extra irrelevant log noise and an unrelated deploy
    e4 = copy.deepcopy(ev)
    e4["log_snippets"] = e4["log_snippets"] + r.sample(NOISE_LOGS, 2)
    e4["recent_deploys"] = e4["recent_deploys"] + [
        {"commit": _commit(r), "minutes_ago": 900, "files": ["docs/README.md"]}
    ]
    out.append(("distractors", e4))
    return [{"kind": k, "evidence": e, "summary": None} for k, e in out]


def _paraphrase_prompt(text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You rewrite incident summaries for an SRE team. Keep every number, metric, config "
                "key and log fact; change wording, order and style. Return JSON "
                '{"paraphrases": ["...", "...", "...", "..."]} with exactly 4 items.'
            ),
        },
        {"role": "user", "content": text},
    ]


def fix_case(r: random.Random, idx: int) -> dict[str, Any]:
    cause = r.choice([c for c in CAUSES if c.action == "pr"])
    k, before, after = cause.diff[0]
    kind = r.choices(
        ["good", "no_recovery", "regression", "wrong_key", "outside_allowlist"],
        [0.5, 0.15, 0.15, 0.1, 0.1],
    )[0]
    target = next(iter(cause.effects))
    base = BASE[target]
    degraded = round(base * (1 + cause.effects[target]), 4)
    patched_val = round(base * (1 + r.uniform(-0.015, 0.015)), 4)
    others = {m: round(BASE[m] * (1 + r.uniform(-0.01, 0.01)), 4) for m in BASE if m != target}
    patch_key, patch_from, patch_to = k, after, before
    path = (cause.files or ["apps/patient-rag/config/rag.yaml"])[-1]
    if kind == "no_recovery":
        patched_val = round(degraded + (base - degraded) * r.uniform(0.1, 0.6), 4)
    elif kind == "regression":
        m = r.choice([x for x in others if x != "tokens_in"])
        sign = 1 if m in ("p95_latency_ms", "error_rate") else -1
        others[m] = round(BASE[m] * (1 + sign * r.uniform(0.1, 0.4)), 4)
    elif kind == "wrong_key":
        patch_key = r.choice([c.diff[0][0] for c in CAUSES if c.diff and c.diff[0][0] != k])
        patched_val = degraded
    elif kind == "outside_allowlist":
        path = "apps/patient-rag/src/retrieval.py"
    diff = (
        f"--- a/{path}\n+++ b/{path}\n@@\n-{patch_key}: {json.dumps(patch_from)}\n"
        f"+{patch_key}: {json.dumps(patch_to)}\n"
    )
    return {
        "id": f"X-{idx:04d}",
        "cause": cause.id,
        "patch_diff": diff,
        "eval": {
            "target_metric": target,
            "baseline": {target: base, **{m: BASE[m] for m in others}},
            "degraded": {target: degraded},
            "patched": {target: patched_val, **others},
        },
        "labels": {"verdict": "accept" if kind == "good" else "reject", "kind": kind},
    }


async def generate(seed: int, llm: LLM) -> dict[str, int]:
    r = rng(seed, "routing")
    n_amb = int(N_REAL * AMBIGUOUS_SHARE)
    amb_flags = [True] * n_amb + [False] * (N_REAL - n_amb)
    r.shuffle(amb_flags)
    real = [real_case(r, i + 1, amb) for i, amb in enumerate(amb_flags)]
    noise = [noise_case(r, i + 1) for i in range(N_NOISE)]
    cases = real + noise

    rp = rng(seed, "routing.perturb")
    chosen = sorted(rp.sample(real, N_PERTURBED), key=lambda c: c["id"])
    paraphrases = await asyncio.gather(
        *(
            complete_json(llm, _paraphrase_prompt(c["summary"]), key=f"routing:para:{c['id']}")
            for c in chosen
        )
    )
    perturbed = []
    for c, para in zip(chosen, paraphrases, strict=True):
        texts = [str(t) for t in (para.get("paraphrases") or [])][:4]
        while len(texts) < 4:
            texts.append(c["summary"])
        variants = [
            {"kind": f"paraphrase{j + 1}", "evidence": c["evidence"], "summary": t}
            for j, t in enumerate(texts)
        ]
        variants += structural_perturbations(rp, c)
        for j, v in enumerate(variants):
            perturbed.append(
                {
                    "id": f"{c['id']}-p{j + 1}",
                    "source_id": c["id"],
                    "kind": v["kind"],
                    "evidence": v["evidence"],
                    "summary": v["summary"] or summary({"evidence": v["evidence"]}),
                    "labels": c["labels"],
                }
            )

    rf = rng(seed, "routing.fixes")
    fixes = [fix_case(rf, i + 1) for i in range(N_FIXES)]

    out = {
        "routing.jsonl": write_jsonl(DATASETS_DIR / "routing.jsonl", cases),
        "routing_perturbations.jsonl": write_jsonl(
            DATASETS_DIR / "routing_perturbations.jsonl", perturbed
        ),
        "fixes.jsonl": write_jsonl(DATASETS_DIR / "fixes.jsonl", fixes),
    }
    counts = {
        "real_cases": len(real),
        "noise_cases": len(noise),
        "ambiguous": n_amb,
        "perturbed_sources": len(chosen),
        "perturbations": len(perturbed),
        "fix_cases": len(fixes),
    }
    for s in SPECIALISTS:
        counts[f"specialist_{s}"] = sum(1 for c in real if c["labels"]["specialist"] == s)
    for sev in ("sev1", "sev2", "sev3", "sev4"):
        counts[f"severity_{sev}"] = sum(1 for c in real if c["labels"]["severity"] == sev)
    for a in ("pr", "runbook", "escalate"):
        counts[f"action_{a}"] = sum(1 for c in real if c["labels"]["action"] == a)
    counts["fix_accept"] = sum(1 for f in fixes if f["labels"]["verdict"] == "accept")
    write_manifest(
        DATASETS_DIR,
        generator="routing",
        version=VERSION,
        seed=seed,
        deployment=llm.deployment,
        counts=counts,
        outputs={k: sha256_file(DATASETS_DIR / k) for k in out},
    )
    return counts


def main() -> None:
    cfg = load_synth_config()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=cfg.seed)
    args = ap.parse_args()
    llm = CachedLLM(cfg.deployment, cfg.concurrency)

    async def run() -> dict[str, int]:
        try:
            return await generate(args.seed, llm)
        finally:
            await aclose(llm)

    print(f"routing: {asyncio.run(run())} (llm calls {llm.calls}, cache hits {llm.hits})")


if __name__ == "__main__":
    main()
