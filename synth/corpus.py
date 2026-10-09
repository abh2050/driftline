"""Corpus generator (spec 3.1): ~300 Cascade Forge Works documents plus 30 revised docs for F5.

Usage: uv run python -m synth.corpus [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from synth.common import (
    DATA_DIR,
    LLM,
    CachedLLM,
    aclose,
    load_synth_config,
    rng,
    sha256_file,
    write_jsonl,
    write_manifest,
)
from synth.topics import Equipment, Fact, TopicGraph, build_topic_graph, changed_value

VERSION = "1.0.0"
OUT_DIR = DATA_DIR / "corpus"
N_CHANGED = 30

DOC_TYPES = {
    "sop": "standard operating procedure",
    "maintenance": "equipment maintenance manual",
    "safety": "safety bulletin",
    "quality": "quality specification",
    "change": "engineering change notice",
    "faq": "engineering FAQ",
}
SOP_OPS = [
    "Line startup",
    "Product changeover",
    "First-article inspection",
    "Shift handover",
    "Planned shutdown",
]
MM_SUBTOPICS = ["Lubrication and fluids", "Fastener inspection and torque", "Calibration checks"]
HAZARDS = [
    "stored hydraulic energy",
    "hot metal and scale ejection",
    "pinch points during die change",
    "arc flash at the induction power cabinet",
    "quench oil fire risk",
    "noise exposure",
    "airborne dust from blasting",
]
PROCESS_SPECS = [
    "Forging temperature control",
    "Quench and temper process window",
    "Ring rolling dimensional control",
    "Shot peening coverage",
    "Machining surface finish",
]

SYSTEM_PROMPT = (
    "You write internal technical documents for Cascade Forge Works, a fictional industrial "
    "forging manufacturer. Write realistic, specific, concise markdown that an engineer would "
    "find in a document control system. Rules: include every given value exactly as written, "
    "with its unit; do not invent any other numeric specification, torque, interval, "
    "temperature, pressure or part number; cite related documents only by the ids given; "
    "start with a level-1 heading containing the document id and title; no preamble."
)


@dataclass
class Doc:
    id: str
    type: str
    title: str
    revision: str
    plant: str | None
    line: str | None
    equipment: list[str]
    parts: list[str]
    facts: list[Fact]
    topic: str
    refs: list[str] = field(default_factory=list)
    version: int = 1
    text: str = ""
    supersedes: str | None = None

    def record(self) -> dict[str, Any]:
        d = asdict(self)
        d["facts"] = [asdict(f) for f in self.facts]
        return d


def _rev(r: random.Random) -> str:
    return "Rev " + r.choice("ABCDE")


def plan_documents(graph: TopicGraph, seed: int) -> list[Doc]:
    """Deterministic document plan (ids, metadata, facts, cross references). No LLM."""
    r = rng(seed, "corpus.plan")
    eq_by_id = graph.equipment_by_id()
    docs: list[Doc] = []

    # 60 SOPs: 5 operations x 12 lines.
    for line in graph.lines:
        for i, op in enumerate(SOP_OPS, start=1):
            eqs = [eq_by_id[e] for e in line.equipment]
            facts = r.sample([f for e in eqs for f in e.facts if "interval" not in f.key], 2)
            docs.append(
                Doc(
                    f"SOP-{line.id}-{i:02d}",
                    "sop",
                    f"{op} - {line.name} line",
                    _rev(r),
                    line.plant,
                    line.id,
                    line.equipment,
                    [],
                    facts,
                    op,
                )
            )
    # 60 maintenance manuals: one per equipment (36) + 24 subtopic manuals.
    for e in graph.equipment:
        docs.append(
            Doc(
                f"MM-{e.id[3:]}",
                "maintenance",
                f"Maintenance manual - {e.name}",
                _rev(r),
                e.plant,
                e.line,
                [e.id],
                [],
                list(e.facts[:2]),
                "overview",
            )
        )
    for k, e in enumerate(r.sample(graph.equipment, 24)):
        sub = MM_SUBTOPICS[k % len(MM_SUBTOPICS)]
        pick = [f for f in e.facts if f not in e.facts[:2]]
        docs.append(
            Doc(
                f"MM-{e.id[3:]}-S{k % 3 + 1}",
                "maintenance",
                f"{sub} - {e.name}",
                _rev(r),
                e.plant,
                e.line,
                [e.id],
                [],
                pick,
                sub,
            )
        )
    # 50 safety bulletins.
    for k in range(50):
        e = r.choice(graph.equipment)
        docs.append(
            Doc(
                f"SB-{1001 + k}",
                "safety",
                f"Safety bulletin: {r.choice(HAZARDS)}",
                _rev(r),
                e.plant,
                e.line,
                [e.id],
                [],
                [],
                "hazard",
            )
        )
    # 50 quality specs: 40 parts + 10 process specs.
    for p in graph.parts:
        docs.append(
            Doc(
                f"QS-{p.number}",
                "quality",
                f"Quality specification - {p.name} {p.number} ({p.material})",
                _rev(r),
                p.plant,
                None,
                [],
                [p.number],
                list(p.facts),
                "part",
            )
        )
    heat = [e for e in graph.equipment if e.type_code in ("HT", "IH")]
    for k in range(10):
        e = heat[k % len(heat)]
        docs.append(
            Doc(
                f"QS-PROC-{k + 1:02d}",
                "quality",
                f"{PROCESS_SPECS[k % 5]} ({e.plant})",
                _rev(r),
                e.plant,
                e.line,
                [e.id],
                [],
                [],
                "process",
            )
        )
    # 50 change notices (historic; they state the current value, not the change).
    for k in range(50):
        e = r.choice(graph.equipment)
        docs.append(
            Doc(
                f"ECN-2026-{k + 1:03d}",
                "change",
                f"Change notice - {e.name}",
                _rev(r),
                e.plant,
                e.line,
                [e.id],
                [],
                [],
                "change",
            )
        )
    # 30 FAQs.
    for k in range(30):
        docs.append(
            Doc(
                f"FAQ-{k + 1:03d}",
                "faq",
                f"Engineering FAQ #{k + 1}",
                "Rev A",
                None,
                None,
                [],
                [],
                [],
                "faq",
            )
        )

    _assign_mentions(graph, docs, r)
    _assign_refs(docs, eq_by_id, r)
    return docs


def _assign_mentions(graph: TopicGraph, docs: list[Doc], r: random.Random) -> None:
    """Give SB/ECN/process/FAQ docs facts, keeping enough exclusive facts for F5."""
    counts = Counter(f.id for d in docs for f in d.facts)
    eq_by_id = graph.equipment_by_id()
    for d in docs:
        if d.type in ("safety", "change") or (d.type == "quality" and d.topic == "process"):
            e = eq_by_id[d.equipment[0]]
            pool = [f for f in e.facts if counts[f.id] > 0] or e.facts
            f = r.choice(pool)
            d.facts = [f]
            counts[f.id] += 1
    shared = [f for f in graph.facts() if counts[f.id] >= 2]
    for d in docs:
        if d.type == "faq":
            d.facts = r.sample(shared, 5)


def _assign_refs(docs: list[Doc], eq_by_id: dict[str, Equipment], r: random.Random) -> None:
    by_eq: dict[str, list[str]] = defaultdict(list)
    for d in docs:
        for e in d.equipment:
            by_eq[e].append(d.id)
    for d in docs:
        cands = sorted({x for e in d.equipment for x in by_eq[e]} - {d.id})
        if d.type == "faq":
            cands = sorted({x for f in d.facts for x in by_eq.get(f.subject, [])})
        d.refs = sorted(r.sample(cands, min(3, len(cands))))


def plan_changes(docs: list[Doc], seed: int) -> list[Doc]:
    """Pick 30 docs whose facts appear nowhere else and revise 1-2 values each (F5)."""
    r = rng(seed, "corpus.changes")
    counts = Counter(f.id for d in docs for f in d.facts)
    eligible = [
        d
        for d in docs
        if d.type in ("sop", "maintenance", "quality") and any(counts[f.id] == 1 for f in d.facts)
    ]
    chosen = sorted(r.sample(eligible, N_CHANGED), key=lambda d: d.id)
    revised: list[Doc] = []
    for d in chosen:
        exclusive = [f for f in d.facts if counts[f.id] == 1]
        to_change = {f.id for f in r.sample(exclusive, min(len(exclusive), r.choice([1, 2])))}
        new_facts = [
            Fact(f.id, f.subject, f.subject_name, f.key, changed_value(r, f), f.unit)
            if f.id in to_change
            else f
            for f in d.facts
        ]
        next_rev = "Rev " + chr(ord(d.revision[-1]) + 1)
        revised.append(
            Doc(
                d.id,
                d.type,
                d.title,
                next_rev,
                d.plant,
                d.line,
                d.equipment,
                d.parts,
                new_facts,
                d.topic,
                d.refs,
                version=2,
                supersedes=d.revision,
            )
        )
    return revised


def _prompt(d: Doc) -> list[dict[str, str]]:
    lines = [
        f"Document type: {DOC_TYPES[d.type]}",
        f"Document id: {d.id}",
        f"Title: {d.title}",
        f"Revision: {d.revision}",
    ]
    if d.plant:
        lines.append(f"Plant: {d.plant}" + (f", line {d.line}" if d.line else ""))
    if d.equipment:
        lines.append("Equipment: " + ", ".join(d.equipment))
    if d.parts:
        lines.append("Parts: " + ", ".join(d.parts))
    if d.supersedes:
        lines.append(f"This revision supersedes {d.supersedes}. Mention that in the header.")
    lines.append("Related documents to cite: " + (", ".join(d.refs) or "none"))
    if d.facts:
        lines.append("Values to include exactly:")
        lines.extend(f"- {f.sentence()}" for f in d.facts)
    style = {
        "sop": "numbered steps with preconditions, PPE and sign-off",
        "maintenance": "sections for scope, tools, procedure, acceptance criteria",
        "safety": "hazard description, affected equipment, required controls, effective date",
        "quality": "scope, requirements table, inspection method, nonconformance handling",
        "change": "reason for change, affected items, implementation, approvals",
        "faq": "five question and answer pairs, one per given value",
    }[d.type]
    lines.append(f"Structure: {style}. Length: 250 to 450 words.")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _missing(d: Doc, text: str) -> list[Fact]:
    return [f for f in d.facts if f.value not in text]


async def render(llm: LLM, d: Doc, attempts: int = 3) -> str:
    text = ""
    for attempt in range(attempts):
        text = await llm.complete(_prompt(d), key=f"corpus:{d.id}:v{d.version}#{attempt}")
        if not _missing(d, text):
            return text.strip() + "\n"
    # Guarantee the ground truth is present: append a deterministic values table.
    table = "\n".join(
        f"| {f.key} | {f.subject} | {f.value} {f.unit}".rstrip() + " |" for f in _missing(d, text)
    )
    header = "## Key values\n\n| Item | Applies to | Value |\n|---|---|---|"
    return f"{text.strip()}\n\n{header}\n{table}\n"


async def generate(seed: int, llm: LLM) -> dict[str, int]:
    graph = build_topic_graph(seed)
    docs = plan_documents(graph, seed)
    changed = plan_changes(docs, seed)
    texts = await asyncio.gather(*(render(llm, d) for d in docs + changed))
    for d, t in zip(docs + changed, texts, strict=True):
        d.text = t
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "graph.json").write_text(_dumps_graph(graph))
    n_docs = write_jsonl(OUT_DIR / "docs.jsonl", (d.record() for d in docs))
    n_changed = write_jsonl(OUT_DIR / "docs_v2.jsonl", (d.record() for d in changed))
    counts = {"documents": n_docs, "documents_v2": n_changed, "facts": len(graph.facts())}
    counts.update({f"type_{k}": v for k, v in Counter(d.type for d in docs).items()})
    write_manifest(
        OUT_DIR,
        generator="corpus",
        version=VERSION,
        seed=seed,
        deployment=llm.deployment,
        counts=counts,
        outputs={
            p: sha256_file(OUT_DIR / p) for p in ("graph.json", "docs.jsonl", "docs_v2.jsonl")
        },
    )
    return counts


def _dumps_graph(graph: TopicGraph) -> str:
    import json

    return json.dumps(graph.to_json(), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


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

    counts = asyncio.run(run())
    print(f"corpus: {counts} (llm calls {llm.calls}, cache hits {llm.hits})")


if __name__ == "__main__":
    main()
