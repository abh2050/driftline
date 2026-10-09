"""Determinism and label sanity for the synthetic generators (spec rule 2.9)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from synth import corpus, routing, telemetry
from synth.common import Message, rng
from synth.topics import build_topic_graph


class FakeLLM:
    """Deterministic stand-in: echoes a hash of the prompt and includes every fact value."""

    deployment = "fake"

    async def complete(
        self, messages: Sequence[Message], *, json_mode: bool = False, key: str = ""
    ) -> str:
        user = messages[-1]["content"]
        h = hashlib.sha256((user + key).encode()).hexdigest()[:8]
        if json_mode:
            return json.dumps(
                {
                    "paraphrases": [f"p{i} {h}" for i in range(4)],
                    "question": f"q {h}",
                    "queries": [],
                }
            )
        return f"# doc {h}\n\n{user}\n"


def _digest(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def test_rng_streams_are_stable_and_independent() -> None:
    assert rng(1, "a").random() == rng(1, "a").random()
    assert rng(1, "a").random() != rng(1, "b").random()
    assert rng(1, "a").random() != rng(2, "a").random()


def test_topic_graph_is_deterministic() -> None:
    a = build_topic_graph(7).to_json()
    assert a == build_topic_graph(7).to_json()
    assert a != build_topic_graph(8).to_json()


async def test_corpus_same_seed_same_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    digests = []
    for run in range(2):
        out = tmp_path / str(run)
        monkeypatch.setattr(corpus, "OUT_DIR", out)
        counts = await corpus.generate(11, FakeLLM())
        assert counts["documents"] == 300
        assert counts["documents_v2"] == corpus.N_CHANGED
        digests.append(_digest(list(out.iterdir())))
    assert digests[0] == digests[1]


def test_corpus_v2_changes_only_exclusive_facts() -> None:
    docs = corpus.plan_documents(build_topic_graph(3), 3)
    changed = corpus.plan_changes(docs, 3)
    v1 = {d.id: d for d in docs}
    owners: dict[str, int] = {}
    for d in docs:
        for f in d.facts:
            owners[f.id] = owners.get(f.id, 0) + 1
    for d in changed:
        diffs = [f for f, o in zip(d.facts, v1[d.id].facts, strict=True) if f.value != o.value]
        assert diffs, d.id
        assert all(owners[f.id] == 1 for f in diffs)


def test_telemetry_same_seed_same_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "DAYS", 1)
    monkeypatch.setattr(telemetry, "TRIALS", 1)
    monkeypatch.setattr(telemetry, "FAULT_IDS", ["F3", "F7"])
    manifests = []
    for run in range(2):
        out = tmp_path / str(run)
        monkeypatch.setattr(telemetry, "OUT_DIR", out)
        counts = telemetry.generate(5)
        assert counts["baseline_events"] == 8640
        manifests.append((out / "manifest.telemetry.json").read_text())
    assert manifests[0] == manifests[1]


def test_routing_labels_follow_rules() -> None:
    r = rng(1, "t")
    cases = [routing.real_case(r, i, ambiguous=i % 5 == 0) for i in range(300)]
    for c in cases:
        cause = routing.CAUSE_BY_ID[c["cause"]]
        assert c["labels"]["specialist"] == cause.specialist
        assert c["labels"]["action"] == cause.action
        assert c["labels"]["severity"] == routing.severity(c["evidence"]["metric_deltas"])
        if c["labels"]["ambiguous"]:
            assert c["labels"]["specialist_secondary"] not in (None, cause.specialist)
        assert c["evidence"]["window_count"] >= 3
    noise = [routing.noise_case(r, i) for i in range(50)]
    assert all(n["evidence"]["window_count"] < 3 and not n["labels"]["is_real"] for n in noise)


def test_fix_cases_have_consistent_verdicts() -> None:
    r = rng(2, "fix")
    for i in range(200):
        f = routing.fix_case(r, i)
        assert (f["labels"]["verdict"] == "accept") == (f["labels"]["kind"] == "good")
