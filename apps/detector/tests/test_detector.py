from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest

from detector.app import config_diff
from detector.simulate import simulate, windows_from_events
from driftline_common.config import load_detector_config
from driftline_common.schemas import TelemetryEvent, Window
from synth.common import read_jsonl

DATA = Path(__file__).resolve().parents[3] / "data" / "telemetry"
CFG = load_detector_config()


@cache
def windows(*names: str) -> list[Window]:
    events = (TelemetryEvent.model_validate(r) for n in names for r in read_jsonl(DATA / n))
    return windows_from_events(events)


def test_no_false_positive_in_48_clean_hours() -> None:
    clean = windows(
        "baseline/day-12.jsonl.gz", "baseline/day-13.jsonl.gz", "baseline/day-14.jsonl.gz"
    )
    incidents, _ = simulate(clean, CFG)
    assert len(incidents) <= 1


@pytest.mark.parametrize("fault", ["F1", "F2", "F3", "F4", "F5", "F6", "F7"])
def test_fault_trials_are_detected(fault: str) -> None:
    warm = windows("baseline/day-14.jsonl.gz")[-720:]
    detected = 0
    for trial in range(1, 6):
        seg = windows(f"faults/{fault}-t{trial}.jsonl.gz")
        onset = next(w.start for w in seg if w.fault_ids)
        incidents, _ = simulate(warm + seg, CFG)
        if any(i.detected_window >= onset.isoformat() and fault in i.fault_ids for i in incidents):
            detected += 1
    assert detected >= 4, f"{fault}: detected {detected}/5"


def test_config_diff() -> None:
    registry = {"a": {"config": {"top_k": 8, "x": 1}}, "b": {"config": {"top_k": 2, "x": 1}}}
    assert config_diff(registry, "a", "b") == [{"key": "top_k", "from": 8, "to": 2}]
    assert config_diff(registry, "a", "a") == []
    assert config_diff(registry, "a", "zz") == [{"key": "config_hash", "from": "a", "to": "zz"}]


@pytest.mark.parametrize("fault", ["F1", "F2", "F3", "F4", "F6"])
def test_config_faults_diff_against_the_pre_fault_config(fault: str) -> None:
    """Gradual faults stay under the CUSUM threshold for a few blocks; the incident must still
    compare against the config that ran before the fault, not the last unbreached block's."""
    warm = windows("baseline/day-14.jsonl.gz")[-720:]
    clean_hash = warm[-1].config_hash
    for trial in range(1, 6):
        incidents, _ = simulate(warm + windows(f"faults/{fault}-t{trial}.jsonl.gz"), CFG)
        for i in incidents:
            if fault in i.fault_ids:
                assert i.baseline_config_hash == clean_hash
                assert i.config_hash != clean_hash
