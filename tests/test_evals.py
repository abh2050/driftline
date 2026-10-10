from __future__ import annotations

from pathlib import Path

import yaml

from evals.layer1.metrics import aggregate, gate

GATES = yaml.safe_load((Path(__file__).parents[1] / "evals" / "gates.yaml").read_text())
BASE = {
    "recall_at_k": 0.95,
    "mrr": 0.8,
    "groundedness": 4.5,
    "relevance": 4.4,
    "response_completeness": 4.2,
    "citation_rate": 0.95,
    "answer_accuracy": 0.9,
    "latency_p95_ms": 6000.0,
    "cost_per_query_usd": 0.002,
}


def test_gate_passes_on_recovery_without_regressions() -> None:
    cand = {**BASE, "groundedness": 4.45}
    assert gate(BASE, cand, "groundedness", GATES).passed


def test_gate_fails_when_degraded_metric_not_recovered() -> None:
    cand = {**BASE, "groundedness": 4.0}
    r = gate(BASE, cand, "groundedness", GATES)
    assert not r.passed
    assert not r.recovered


def test_gate_fails_on_other_regression() -> None:
    cand = {**BASE, "latency_p95_ms": 9000.0}
    r = gate(BASE, cand, "groundedness", GATES)
    assert not r.passed
    assert "latency_p95_ms" in r.regressions


def test_aggregate() -> None:
    rows = [
        {
            "source_doc_ids": ["A"],
            "retrieved_doc_ids": ["B", "A"],
            "answer": "x 12 y",
            "expected": "12",
            "citations": ["A"],
            "latency_ms": 100.0,
            "cost_usd": 0.001,
            "error": None,
            "groundedness": 5.0,
            "relevance": 4.0,
            "response_completeness": 3.0,
        },
        {
            "source_doc_ids": ["C"],
            "retrieved_doc_ids": ["D"],
            "answer": "no",
            "expected": "7",
            "citations": [],
            "latency_ms": 300.0,
            "cost_usd": 0.003,
            "error": None,
            "groundedness": 3.0,
            "relevance": 2.0,
            "response_completeness": 1.0,
        },
    ]
    m = aggregate(rows)
    assert m["recall_at_k"] == 0.5
    assert m["mrr"] == 0.25
    assert m["answer_accuracy"] == 0.5
    assert m["citation_rate"] == 0.5
    assert m["groundedness"] == 4.0
