from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from driftline_common.decision import CATALOG, Decision, normalize, top_two
from evals.layer2a_router.run import calibration, macro_f1
from router.clients import RulesRouterClient, _parse, find_scores

DATA = Path(__file__).resolve().parents[3] / "evals" / "datasets"


def test_normalize_sums_to_one_and_handles_junk() -> None:
    s = normalize({"Real": 3, "noise": 1, "other": 9}, ["real", "noise"])
    assert abs(sum(s.values()) - 1) < 1e-6
    assert s["real"] > s["noise"]
    assert normalize({}, ["a", "b"]) == {"a": 0.5, "b": 0.5}
    assert normalize({"a": "x", "b": -1}, ["a", "b"]) == {"a": 0.5, "b": 0.5}


def test_top_two() -> None:
    assert top_two({"a": 0.6, "b": 0.3, "c": 0.1}) == ("a", 0.3)


def test_decision_validates_options() -> None:
    with pytest.raises(ValidationError):
        Decision(
            question_id="q",
            options=["a", "b"],
            scores={"a": 1.0},
            chosen="a",
            margin=1,
            latency_ms=1,
            model="m",
            input_tokens=0,
            cost_usd=0,
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"scores": {"a": 0.9, "b": 0.1}, "rationale": "r"}',
        '{"final": {"scores": {"a": 0.9, "b": 0.1}, "rationale": "r"}}',
        '{"final": "{\\"scores\\": {\\"a\\": 0.9, \\"b\\": 0.1}, \\"rationale\\": \\"r\\"}"}',
        'Sure: {"scores": {"a": 0.9, "b": 0.1}, "rationale": "r"}',
    ],
)
def test_find_scores_variants(text: str) -> None:
    scores, why = find_scores(_parse(text))
    assert scores == {"a": 0.9, "b": 0.1}
    assert why == "r"


def test_find_scores_invalid() -> None:
    assert find_scores(_parse('{"": ""}'))[0] is None


async def test_rules_client_runs_every_type() -> None:
    case = json.loads((DATA / "routing.jsonl").read_text().splitlines()[0])
    client = RulesRouterClient()
    for dtype, spec in CATALOG.items():
        d = await client.decide(
            spec.question,
            list(spec.options),
            case["evidence"],
            question_id="x",
            decision_type=dtype,
        )
        assert d.chosen in spec.options
        assert d.cost_usd == 0


def test_calibration_perfect_and_overconfident() -> None:
    ece, diagram = calibration([(0.95, True)] * 19 + [(0.95, False)])
    assert ece == pytest.approx(0.0, abs=1e-9)
    assert sum(b["count"] for b in diagram) == 20
    ece_bad, _ = calibration([(0.9, False)] * 10)
    assert ece_bad == pytest.approx(0.9)


def test_macro_f1() -> None:
    pairs = [("a", "a"), ("b", "b"), ("a", "b")]
    assert 0 < macro_f1(pairs, ["a", "b"]) < 1
