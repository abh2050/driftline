"""Golden QA set (spec 3.2): 100 standard questions + 50 that depend on the F5 changed docs.

Usage: uv run python -m synth.golden [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
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
OUT = DATASETS_DIR / "golden.jsonl"
N_STANDARD = 100
N_FRESH = 50
STYLES = [
    "a direct question",
    "a question from someone in a hurry",
    "a question that names the equipment or part id",
    "a question that names the equipment or part by description",
    "a question phrased as a check before doing work",
]


def _prompt(fact: dict[str, Any], style: str) -> list[dict[str, str]]:
    unit = f" {fact['unit']}" if fact["unit"] else ""
    return [
        {
            "role": "system",
            "content": (
                "You write test questions for a document search assistant at Cascade Forge Works, "
                'a fictional forging manufacturer. Return JSON {"question": "..."}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Write {style} whose answer is this fact. Do not include the value itself.\n"
                f"Fact: the {fact['key']} for {fact['subject_name']} ({fact['subject']}) "
                f"is {fact['value']}{unit}."
            ),
        },
    ]


def _answer(fact: dict[str, Any], doc_id: str, revision: str) -> str:
    unit = f" {fact['unit']}" if fact["unit"] else ""
    return (
        f"The {fact['key']} for {fact['subject_name']} ({fact['subject']}) is "
        f"{fact['value']}{unit}, per {doc_id} {revision}."
    )


async def generate(seed: int, llm: LLM) -> dict[str, int]:
    docs = read_jsonl(DATA_DIR / "corpus" / "docs.jsonl")
    v2 = read_jsonl(DATA_DIR / "corpus" / "docs_v2.jsonl")
    v1_by_id = {d["id"]: d for d in docs}
    r = rng(seed, "golden")

    fact_docs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in docs:
        for f in d["facts"]:
            fact_docs[f["id"]].append(d)

    fresh: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    changed_ids: set[str] = set()
    for d in v2:
        old = {f["id"]: f for f in v1_by_id[d["id"]]["facts"]}
        for f in d["facts"]:
            if f["value"] != old[f["id"]]["value"]:
                fresh.append((f, d, old[f["id"]]["value"]))
                changed_ids.add(f["id"])
    stable = sorted(fid for fid in fact_docs if fid not in changed_ids)
    standard_ids = r.sample(stable, N_STANDARD)
    fresh = sorted(fresh, key=lambda x: x[0]["id"])
    while len(fresh) < N_FRESH:  # reuse changed facts with a different question style
        fresh.append(fresh[len(fresh) % max(1, len(fresh))])
    fresh = fresh[:N_FRESH]

    items: list[dict[str, Any]] = []
    for i, fid in enumerate(standard_ids):
        src = sorted(fact_docs[fid], key=lambda d: d["id"])
        fact = next(f for f in src[0]["facts"] if f["id"] == fid)
        items.append(
            {
                "id": f"G-{i + 1:03d}",
                "kind": "standard",
                "fact": fact,
                "source_doc_ids": [d["id"] for d in src],
                "home": src[0],
                "style": r.choice(STYLES),
                "stale_value": None,
            }
        )
    for i, (fact, doc, old_value) in enumerate(fresh):
        items.append(
            {
                "id": f"G-{N_STANDARD + i + 1:03d}",
                "kind": "fresh",
                "fact": fact,
                "source_doc_ids": [doc["id"]],
                "home": doc,
                "style": r.choice(STYLES),
                "stale_value": old_value,
            }
        )

    questions = await asyncio.gather(
        *(
            complete_json(llm, _prompt(it["fact"], it["style"]), key=f"golden:{it['id']}")
            for it in items
        )
    )
    records = []
    for it, q in zip(items, questions, strict=True):
        home = it["home"]
        records.append(
            {
                "id": it["id"],
                "kind": it["kind"],
                "question": str(q["question"]).strip(),
                "reference_answer": _answer(it["fact"], home["id"], home["revision"]),
                "answer_value": it["fact"]["value"],
                "stale_value": it["stale_value"],
                "fact_id": it["fact"]["id"],
                "source_doc_ids": it["source_doc_ids"],
                "reviewed": False,
            }
        )
    n = write_jsonl(OUT, records)
    counts = {"questions": n, "standard": N_STANDARD, "fresh": N_FRESH}
    write_manifest(
        DATASETS_DIR,
        generator="golden",
        version=VERSION,
        seed=seed,
        deployment=llm.deployment,
        counts=counts,
        outputs={"golden.jsonl": sha256_file(OUT)},
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

    print(f"golden: {asyncio.run(run())} (llm calls {llm.calls}, cache hits {llm.hits})")


if __name__ == "__main__":
    main()
