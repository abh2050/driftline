"""Review CLI for the golden QA set (spec 3.2).

Shows each unreviewed record with its source fact and lets you accept, edit or skip it.
Review state lives in evals/datasets/golden_reviews.jsonl so regenerating golden.jsonl
(which always writes reviewed=false) does not lose your work; `apply` merges it back.

Usage:
  uv run python -m synth.review            # interactive review
  uv run python -m synth.review status     # counts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from synth.common import DATA_DIR, DATASETS_DIR, read_jsonl, write_jsonl

GOLDEN = DATASETS_DIR / "golden.jsonl"
REVIEWS = DATASETS_DIR / "golden_reviews.jsonl"


def load_reviews(path: Path = REVIEWS) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {r["id"]: r for r in read_jsonl(path)}


def apply_reviews(
    records: list[dict[str, Any]], reviews: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for rec in records:
        rv = reviews.get(rec["id"])
        if rv is not None:
            rec = {**rec, "reviewed": True}
            if rv.get("question"):
                rec["question"] = rv["question"]
            if rv.get("status") == "rejected":
                rec["rejected"] = True
        out.append(rec)
    return out


def _fact_text(fact_id: str) -> str:
    for name in ("docs_v2.jsonl", "docs.jsonl"):
        for d in read_jsonl(DATA_DIR / "corpus" / name):
            for f in d["facts"]:
                if f["id"] == fact_id:
                    return f"{d['id']} {d['revision']}: {f['key']} = {f['value']} {f['unit']}"
    return "(fact not found)"


def interactive() -> None:
    records = read_jsonl(GOLDEN)
    reviews = load_reviews()
    todo = [r for r in records if r["id"] not in reviews]
    print(
        f"{len(reviews)} reviewed, {len(todo)} to go. Keys: a=accept e=edit r=reject s=skip q=quit"
    )
    for rec in todo:
        print("\n" + "-" * 72)
        print(f"{rec['id']} [{rec['kind']}]")
        print(f"Q: {rec['question']}")
        print(f"A: {rec['reference_answer']}")
        print(f"Source: {_fact_text(rec['fact_id'])}")
        if rec["stale_value"]:
            print(f"Stale (pre-F5) value: {rec['stale_value']}")
        choice = input("> ").strip().lower()
        if choice == "q":
            break
        if choice == "s" or choice not in ("a", "e", "r"):
            continue
        review: dict[str, Any] = {"id": rec["id"], "status": "accepted"}
        if choice == "e":
            review["question"] = input("New question: ").strip() or rec["question"]
            review["status"] = "edited"
        if choice == "r":
            review["status"] = "rejected"
        reviews[rec["id"]] = review
        write_jsonl(REVIEWS, sorted(reviews.values(), key=lambda x: x["id"]))
    write_jsonl(GOLDEN, apply_reviews(read_jsonl(GOLDEN), reviews))
    status()


def status() -> None:
    records = read_jsonl(GOLDEN)
    reviews = load_reviews()
    by = {
        s: sum(1 for r in reviews.values() if r["status"] == s)
        for s in ("accepted", "edited", "rejected")
    }
    print(json.dumps({"total": len(records), "reviewed": len(reviews), **by}))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", nargs="?", default="review", choices=["review", "status"])
    args = ap.parse_args()
    if args.command == "status":
        status()
    elif sys.stdin.isatty():
        interactive()
    else:
        sys.exit("review needs an interactive terminal")


if __name__ == "__main__":
    main()
