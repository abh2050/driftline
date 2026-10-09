"""Traffic queries (spec 3.3): 2,000 queries from 6 personas with typos and seasonality.

Writes evals/datasets/traffic.jsonl and evals/datasets/seasonality.json.
Usage: uv run python -m synth.traffic [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
from typing import Any

from synth.common import (
    DATA_DIR,
    DATASETS_DIR,
    LLM,
    CachedLLM,
    aclose,
    complete_json,
    load_synth_config,
    read_jsonl,
    rng,
    sha256_file,
    write_jsonl,
    write_manifest,
)

VERSION = "1.0.0"
N_QUERIES = 2000
BATCH = 20

# persona -> (share of traffic, typo rate, voice, preferred doc types, peak hours)
PERSONAS: dict[str, dict[str, Any]] = {
    "maintenance_tech": {
        "share": 0.30,
        "typo": 0.35,
        "types": ["maintenance", "sop", "safety"],
        "voice": "terse, on the shop floor, often on a phone, uses jargon",
        "peaks": [6, 14, 22],
    },
    "quality_engineer": {
        "share": 0.20,
        "typo": 0.10,
        "types": ["quality", "change", "sop"],
        "voice": "precise, cites part numbers and tolerances",
        "peaks": [9, 13],
    },
    "safety_officer": {
        "share": 0.12,
        "typo": 0.08,
        "types": ["safety", "sop", "maintenance"],
        "voice": "formal, focused on hazards, PPE and lockout",
        "peaks": [8, 15],
    },
    "plant_manager": {
        "share": 0.10,
        "typo": 0.12,
        "types": ["change", "quality", "faq"],
        "voice": "high level, asks about status, changes and impact",
        "peaks": [8, 16],
    },
    "new_hire": {
        "share": 0.18,
        "typo": 0.25,
        "types": ["faq", "sop", "safety"],
        "voice": "unsure, verbose, sometimes asks basic questions",
        "peaks": [9, 11, 14],
    },
    "auditor": {
        "share": 0.10,
        "typo": 0.05,
        "types": ["quality", "change", "sop"],
        "voice": "asks for revisions, approvals and evidence",
        "peaks": [10, 14],
    },
}
WEEKDAY = [1.0, 1.05, 1.05, 1.0, 0.9, 0.35, 0.25]  # Mon..Sun
KEYBOARD_NEIGHBORS = {
    "a": "qs",
    "e": "wr",
    "i": "uo",
    "o": "ip",
    "n": "bm",
    "r": "et",
    "t": "ry",
    "s": "ad",
    "l": "k",
    "c": "xv",
    "u": "yi",
    "m": "n",
    "p": "o",
    "h": "gj",
    "d": "sf",
}


def seasonality() -> dict[str, Any]:
    """Hourly weight per persona (sum of Gaussian bumps around peak hours plus a floor)."""
    hourly = {}
    for name, p in PERSONAS.items():
        w = [0.05 + sum(math.exp(-((h - pk) ** 2) / 4.0) for pk in p["peaks"]) for h in range(24)]
        total = sum(w)
        hourly[name] = [round(x / total, 6) for x in w]
    return {
        "hourly": hourly,
        "weekday": WEEKDAY,
        "persona_share": {k: v["share"] for k, v in PERSONAS.items()},
    }


def add_typos(r: random.Random, text: str, rate: float) -> tuple[str, bool]:
    if r.random() >= rate:
        return text, False
    chars = list(text)
    for _ in range(r.choice([1, 1, 2])):
        idxs = [i for i, c in enumerate(chars) if c.isalpha()]
        if not idxs:
            break
        i = r.choice(idxs)
        op = r.choice(["swap", "drop", "neighbor", "double"])
        c = chars[i].lower()
        if op == "swap" and i + 1 < len(chars):
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        elif op == "drop":
            del chars[i]
        elif op == "neighbor" and c in KEYBOARD_NEIGHBORS:
            chars[i] = r.choice(KEYBOARD_NEIGHBORS[c])
        else:
            chars.insert(i, chars[i])
    out = "".join(chars)
    if r.random() < 0.5:
        out = out.lower()
    return out, True


def _prompt(persona: str, docs: list[dict[str, Any]]) -> list[dict[str, str]]:
    p = PERSONAS[persona]
    topics = "\n".join(f"- {d['id']}: {d['title']}" for d in docs)
    return [
        {
            "role": "system",
            "content": (
                "You simulate employees of Cascade Forge Works, a fictional forging manufacturer, "
                "typing questions into an internal document assistant. Return JSON "
                '{"queries": [{"doc_id": "...", "query": "..."}, ...]} with exactly one '
                "query per listed document, in the same order."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Persona: {persona.replace('_', ' ')} ({p['voice']}).\n"
                "Vary phrasing, length and intent (lookups, how-to, why, comparisons, follow-ups). "
                "Do not invent numbers.\nDocuments:\n" + topics
            ),
        },
    ]


async def generate(seed: int, llm: LLM) -> dict[str, int]:
    docs = read_jsonl(DATA_DIR / "corpus" / "docs.jsonl")
    r = rng(seed, "traffic")
    names = list(PERSONAS)
    counts = {n: int(PERSONAS[n]["share"] * N_QUERIES) for n in names}
    counts[names[0]] += N_QUERIES - sum(counts.values())
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for n in names:
        pool = [d for d in docs if d["type"] in PERSONAS[n]["types"]]
        remaining = counts[n]
        while remaining > 0:
            k = min(BATCH, remaining)
            batches.append((n, r.sample(pool, k)))
            remaining -= k
    results = await asyncio.gather(
        *(complete_json(llm, _prompt(n, b), key=f"traffic:{i}") for i, (n, b) in enumerate(batches))
    )
    records: list[dict[str, Any]] = []
    for (persona, batch), res in zip(batches, results, strict=True):
        queries = res.get("queries", []) if isinstance(res, dict) else []
        for j, d in enumerate(batch):
            q = (
                queries[j]["query"]
                if j < len(queries) and isinstance(queries[j], dict)
                else f"what does {d['id']} say"
            )
            text, typo = add_typos(r, str(q).strip(), PERSONAS[persona]["typo"])
            records.append(
                {
                    "persona": persona,
                    "query": text,
                    "typo": typo,
                    "doc_id": d["id"],
                    "doc_type": d["type"],
                }
            )
    r.shuffle(records)
    for i, rec in enumerate(records):
        rec["id"] = f"Q-{i + 1:04d}"
    out = DATASETS_DIR / "traffic.jsonl"
    n_written = write_jsonl(out, records)
    season = DATASETS_DIR / "seasonality.json"
    season.write_text(json.dumps(seasonality(), indent=1, sort_keys=True) + "\n")
    stats: dict[str, int] = {"queries": n_written, "typos": sum(1 for x in records if x["typo"])}
    stats.update({f"persona_{k}": v for k, v in counts.items()})
    write_manifest(
        DATASETS_DIR,
        generator="traffic",
        version=VERSION,
        seed=seed,
        deployment=llm.deployment,
        counts=stats,
        outputs={"traffic.jsonl": sha256_file(out), "seasonality.json": sha256_file(season)},
    )
    return stats


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

    print(f"traffic: {asyncio.run(run())} (llm calls {llm.calls}, cache hits {llm.hits})")


if __name__ == "__main__":
    main()
