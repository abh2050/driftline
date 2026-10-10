"""Layer 1: RAG quality on the golden set (spec 7.1).

Live mode builds patient-rag in process with the requested config (baseline, a fault, or
explicit overrides), answers every golden question, judges groundedness / relevance /
response completeness with azure-ai-evaluation (uploaded to Foundry evaluations), and writes
a fixture so offline mode can recompute every metric without Azure calls.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from driftline_common.pricing import Pricing
from evals.layer1.metrics import aggregate

EVALS_DIR = Path(__file__).resolve().parents[1]
FIXTURES = EVALS_DIR / "fixtures" / "layer1"
REPORTS = EVALS_DIR / "reports"
GOLDEN = EVALS_DIR / "datasets" / "golden.jsonl"
JUDGE_METRICS = ("groundedness", "relevance", "response_completeness")


def read_fixture(label: str) -> list[dict[str, Any]]:
    """Fixtures are stored as <label>.jsonl, or gzipped when large (fault runs with long
    chunks exceed the repository's file size limit)."""
    plain, packed = FIXTURES / f"{label}.jsonl", FIXTURES / f"{label}.jsonl.gz"
    text = plain.read_text() if plain.exists() else gzip.decompress(packed.read_bytes()).decode()
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def write_fixture(label: str, rows: list[dict[str, Any]]) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    plain, packed = FIXTURES / f"{label}.jsonl", FIXTURES / f"{label}.jsonl.gz"
    if label == "baseline":  # read uncompressed by sandbox-runner
        plain.write_text(text)
        return
    packed.write_bytes(gzip.compress(text.encode(), mtime=0))
    plain.unlink(missing_ok=True)


def load_golden(limit: int | None) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in GOLDEN.read_text().splitlines() if x.strip()]
    rows = [r for r in rows if not r.get("rejected")]
    return rows[:limit] if limit else rows


def expected_value(g: dict[str, Any], source_version: int) -> str:
    if g["kind"] == "fresh" and source_version < 2:
        return str(g["stale_value"])
    return str(g["answer_value"])


async def answer_all(
    golden: list[dict[str, Any]],
    overrides: dict[str, Any],
    source_version: int,
    concurrency: int,
    index_name: str | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    from patient_rag.clients import Clients
    from patient_rag.config import apply, load_base_config
    from patient_rag.pipeline import RagPipeline

    cfg = apply(load_base_config(), overrides)
    clients = Clients(max_retries=8)
    pipeline = RagPipeline(clients, cfg, index_name=index_name)
    pricing = Pricing.load()
    sem = asyncio.Semaphore(concurrency)

    async def one(g: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            r = await pipeline.ask(g["question"])
        cost = pricing.chat(cfg.chat_deployment, r.tokens_in, r.tokens_out)
        if r.reranked:
            cost += pricing.rerank(cfg.reranker_deployment, 1)
        return {
            "id": g["id"],
            "kind": g["kind"],
            "question": g["question"],
            "source_doc_ids": g["source_doc_ids"],
            "expected": expected_value(g, source_version),
            "reference_answer": g["reference_answer"],
            "answer": r.answer,
            "context": r.context,
            "retrieved_doc_ids": r.retrieved_doc_ids,
            "citations": r.citations,
            "latency_ms": round(r.latency_ms, 1),
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "cost_usd": round(cost, 7),
            "error": r.error,
        }

    try:
        rows = await asyncio.gather(*(one(g) for g in golden))
    finally:
        await pipeline.close()
        await clients.close()
    return list(rows), cfg


def judge(rows: list[dict[str, Any]], *, label: str, upload: bool) -> str | None:
    """Score rows with azure-ai-evaluation; returns the Foundry studio URL when uploaded."""
    from azure.ai.evaluation import (
        GroundednessEvaluator,
        RelevanceEvaluator,
        ResponseCompletenessEvaluator,
        evaluate,
    )
    from azure.identity import DefaultAzureCredential

    from patient_rag.config import load_base_config

    cred = DefaultAzureCredential()
    model = {
        "azure_endpoint": os.environ["FOUNDRY_ENDPOINT"],
        "azure_deployment": load_base_config().judge_deployment,
    }
    kw: dict[str, Any] = {"credential": cred, "is_reasoning_model": True}
    evaluators = {
        "groundedness": GroundednessEvaluator(model, **kw),  # type: ignore[no-untyped-call]
        "relevance": RelevanceEvaluator(model, **kw),  # type: ignore[no-untyped-call]
        "response_completeness": ResponseCompletenessEvaluator(model, **kw),
    }
    ok = [r for r in rows if not r["error"]]
    # promptflow batch workers; keep low so the judge deployment is not throttled
    os.environ.setdefault("PF_WORKER_COUNT", "3")
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data.jsonl"
        data.write_text(
            "".join(
                json.dumps(
                    {
                        "id": r["id"],
                        "query": r["question"],
                        "response": r["answer"],
                        "context": r["context"],
                        "ground_truth": r["reference_answer"],
                    }
                )
                + "\n"
                for r in ok
            )
        )
        result = evaluate(
            data=str(data),
            evaluators=evaluators,  # type: ignore[arg-type]
            evaluation_name=f"layer1-{label}",
            azure_ai_project=os.environ.get("FOUNDRY_PROJECT_ENDPOINT") if upload else None,
            evaluator_config={
                "groundedness": {
                    "column_mapping": {
                        "query": "${data.query}",
                        "response": "${data.response}",
                        "context": "${data.context}",
                    }
                },
                "relevance": {
                    "column_mapping": {"query": "${data.query}", "response": "${data.response}"}
                },
                "response_completeness": {
                    "column_mapping": {
                        "response": "${data.response}",
                        "ground_truth": "${data.ground_truth}",
                    }
                },
            },
        )
    by_id = {row["inputs.id"]: row for row in result["rows"]}
    for r in ok:
        out = by_id.get(r["id"], {})
        for m in JUDGE_METRICS:
            v = out.get(f"outputs.{m}.{m}")
            r[m] = None if v is None or v != v else float(v)  # NaN -> None
    url = result.get("studio_url")
    return str(url) if url else None


def write_report(label: str, metrics: dict[str, float], meta: dict[str, Any]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    base = REPORTS / f"{date}-layer1-{label}"
    base.with_suffix(".json").write_text(
        json.dumps({"label": label, "metrics": metrics, **meta}, indent=2, sort_keys=True) + "\n"
    )
    lines = [f"# Layer 1 report: {label}", "", f"Date: {date}", ""]
    for k, v in meta.items():
        if k != "config":
            lines.append(f"- {k}: {v}")
    lines += ["", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in metrics.items()]
    lines += [
        "",
        "Config:",
        "",
        "```yaml",
        yaml.safe_dump(meta.get("config", {}), sort_keys=True).strip(),
        "```",
        "",
    ]
    base.with_suffix(".md").write_text("\n".join(lines))
    return base.with_suffix(".md")


def run_live(
    label: str,
    overrides: dict[str, Any],
    *,
    source_version: int,
    limit: int | None,
    concurrency: int,
    upload: bool,
) -> dict[str, float]:
    golden = load_golden(limit)
    rows, cfg = asyncio.run(answer_all(golden, overrides, source_version, concurrency))
    studio_url = judge(rows, label=label, upload=upload)
    write_fixture(label, rows)
    metrics = aggregate(rows)
    write_report(
        label,
        metrics,
        {
            "mode": "live",
            "config_hash": cfg.config_hash(),
            "config": cfg.model_dump(),
            "source_version": source_version,
            "foundry_evaluation": studio_url,
        },
    )
    return metrics


def run_rejudge(label: str, *, upload: bool) -> dict[str, float]:
    """Re-score a stored fixture's answers with the judge (no new pipeline calls)."""
    rows = read_fixture(label)
    studio_url = judge(rows, label=label, upload=upload)
    write_fixture(label, rows)
    metrics = aggregate(rows)
    previous = sorted(REPORTS.glob(f"*-layer1-{label}.json"))
    meta: dict[str, Any] = json.loads(previous[-1].read_text()) if previous else {}
    meta.pop("metrics", None)
    meta.pop("label", None)
    meta["foundry_evaluation"] = studio_url
    for p in previous:
        p.unlink()
        p.with_suffix(".md").unlink(missing_ok=True)
    write_report(label, metrics, meta)
    return metrics


def run_offline(label: str) -> dict[str, float]:
    return aggregate(read_fixture(label))
