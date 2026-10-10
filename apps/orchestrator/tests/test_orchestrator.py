from __future__ import annotations

import re
from pathlib import Path

import pytest

from orchestrator.patching import PatchError, patch_rag_yaml
from orchestrator.prbody import render
from orchestrator.workflow import _file_and_sha, _pr_url

RAG = (Path(__file__).resolve().parents[2] / "patient-rag" / "config" / "rag.yaml").read_text()


def test_patch_sets_key_pins_and_bumps_revision() -> None:
    current = int(re.search(r"^config_revision: (\d+)", RAG, re.M).group(1))  # type: ignore[union-attr]
    out = patch_rag_yaml(RAG, {"top_k": 8}, "inc-1")
    assert "top_k: 8" in out
    assert "pinned by Driftline inc-1" in out
    assert f"config_revision: {current + 1}" in out
    assert out.count("\n") == RAG.count("\n")  # comments and order preserved


def test_patch_rejects_unknown_or_protected_keys() -> None:
    with pytest.raises(PatchError):
        patch_rag_yaml(RAG, {"rm_rf": True}, "inc-1")
    with pytest.raises(PatchError):
        patch_rag_yaml(RAG, {"config_revision": 99}, "inc-1")


def test_patch_renders_booleans_and_strings() -> None:
    out = patch_rag_yaml(RAG, {"reranker_enabled": False, "prompt_version": "v3"}, "inc-2")
    assert "reranker_enabled: false" in out
    assert "prompt_version: v3" in out


def test_file_and_sha_parsing() -> None:
    items = ["successfully downloaded text file (SHA: " + "a" * 40 + ")", "top_k: 8\n"]
    assert _file_and_sha(items) == ("top_k: 8\n", "a" * 40)
    with pytest.raises(ValueError, match="unexpected"):
        _file_and_sha(["nothing"])


def test_pr_url_extraction() -> None:
    assert (
        _pr_url(['{"html_url": "https://github.com/abh2050/driftline/pull/7"}'])
        == "https://github.com/abh2050/driftline/pull/7"
    )


def test_pr_body_contains_required_sections() -> None:
    run = {
        "incident": {
            "id": "inc-9",
            "metric_deltas": {
                "groundedness": {"baseline": 4.4, "current": 3.1, "delta_pct": -29.5}
            },
        },
        "rca": {
            "root_cause": "top_k reduced to 2",
            "confidence": 0.9,
            "evidence": [{"source": "config_diff", "detail": "top_k 8 -> 2"}],
        },
        "decisions": {
            "D3": {
                "chosen": "retrieval",
                "margin": 0.6,
                "model": "gpt-5-mini",
                "scores": {"retrieval": 0.8, "prompt": 0.2},
            }
        },
        "specialist": "retrieval",
        "costs": {"tokens_usd": 0.01, "decisions_usd": 0.001},
    }
    body = render(run, "Summary.", ["-top_k: 2", "+top_k: 8"], "0" * 32, None)
    for needle in (
        "Root cause",
        "Evidence",
        "**retrieval** | 0.800",
        "Cost",
        "Trace:",
        "-top_k: 2",
        "A human must review",
    ):
        assert needle in body


def test_shield_middleware_is_function_middleware() -> None:
    from agent_framework import Agent

    from orchestrator.safety import shield_middleware

    mw = shield_middleware(None, None, "inc", "agent-x")  # type: ignore[arg-type]
    # Agent classifies middleware at construction; an unclassifiable one raises here
    Agent(client=object(), middleware=[mw])  # type: ignore[arg-type]


def test_config_change_keys_are_bare() -> None:
    from orchestrator.models import ConfigChange

    assert ConfigChange(key="apps/patient-rag/config/rag.yaml:prompt_version", value="v3").key == (
        "prompt_version"
    )
    assert ConfigChange(key=" top_k ", value="8").key == "top_k"
