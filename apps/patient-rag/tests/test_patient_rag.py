from __future__ import annotations

import pytest

from driftline_common.pricing import Pricing
from patient_rag.app import AskRequest, build_event
from patient_rag.chunking import chunk_text
from patient_rag.config import apply, load_base_config, load_fault
from patient_rag.pipeline import CITATION, Hit, RagResult

FAULTS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7"]


def test_baseline_and_index_naming() -> None:
    cfg = load_base_config()
    assert cfg.index_name() == "cfw-c512-o64"
    assert apply(cfg, load_fault("F1").overrides).index_name() == "cfw-c2048-o64"
    # F2 changes the embedding but must keep querying the old index (no reindex).
    assert apply(cfg, load_fault("F2").overrides).index_name() == cfg.index_name()


@pytest.mark.parametrize("fault_id", FAULTS)
def test_every_fault_loads_and_changes_something(fault_id: str) -> None:
    base = load_base_config()
    fault = load_fault(fault_id)
    cfg = apply(base, fault.overrides)
    assert fault.overrides or fault.state
    if fault.overrides:
        assert cfg.config_hash() != base.config_hash()
    assert cfg.prompt()  # prompt file exists


def test_unknown_fault() -> None:
    with pytest.raises(KeyError):
        load_fault("F99")


def test_citation_pattern() -> None:
    text = "Torque is 900 N·m [doc:MM-P1-L1-HP01] and see [doc:QS-CFW-1234-56]."
    assert CITATION.findall(text) == ["MM-P1-L1-HP01", "QS-CFW-1234-56"]


def test_chunking_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(3000))
    chunks = chunk_text(text, 512, 64)
    assert len(chunks) > 3
    assert chunk_text("short text", 512, 64) == ["short text"]


def _result(answer: str, error: str | None = None) -> RagResult:
    hits = [Hit("D1", "D1--0", "t", "c", 0.8), Hit("D2", "D2--0", "t", "c", 0.6)]
    return RagResult(
        answer=answer,
        hits=hits,
        citations=["D1"],
        latency_ms=100,
        retrieval_latency_ms=40,
        llm_latency_ms=60,
        tokens_in=1000,
        tokens_out=100,
        embedding_tokens=10,
        reranked=False,
        config_hash="abc",
        prompt_version="v3",
        error=error,
    )


def test_build_event_canary_and_cost() -> None:
    cfg = load_base_config()
    pricing = Pricing.load()
    ev = build_event(
        AskRequest(query="q", expected=["1200"]), _result("It is 1200 N·m."), 4.5, pricing, cfg
    )
    assert ev.canary_correct is True
    assert ev.top_score == 0.8
    assert ev.mean_score == 0.7
    assert ev.cost_usd > 0
    assert not ev.error
    bad = build_event(
        AskRequest(query="q", expected=["1200"]),
        _result("", error="RateLimitError: 429"),
        None,
        pricing,
        cfg,
    )
    assert bad.error
    assert bad.status_code == 429
    assert bad.canary_correct is False
