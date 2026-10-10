"""Telemetry replay data (spec 3.4).

14 days of clean per-request telemetry at one request every 10 seconds, with diurnal
load, weekly seasonality and Gaussian noise, plus 5 labeled trial segments per fault.
Fault shifts come from synth/fault_effects.yaml (ADR 0002).

Outputs (gzipped JSONL, one event per line, validated against TelemetryEvent):
  data/telemetry/baseline/day-01.jsonl.gz .. day-14.jsonl.gz
  data/telemetry/faults/F<n>-t<k>.jsonl.gz   (2h clean lead-in + 4h faulted)

Usage: uv run python -m synth.telemetry [--seed N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml

from driftline_common.schemas import TelemetryEvent
from synth.common import DATA_DIR, DATASETS_DIR, REPO_ROOT, rng, write_jsonl_gz, write_manifest

VERSION = "1.0.0"
OUT_DIR = DATA_DIR / "telemetry"
START = datetime(2026, 9, 21, tzinfo=UTC)  # a Monday
DAYS = 14
STEP_S = 10
FAULT_IDS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7"]
TRIALS = 5
LEAD_S = 2 * 3600
FAULT_S = 4 * 3600
SEGMENT_START = START + timedelta(days=DAYS)  # fault trials happen after the baseline
PRICE_IN = 0.40 / 1e6  # gpt-4.1-mini USD per token, used only for synthetic cost
PRICE_OUT = 1.60 / 1e6


def load_effects() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO_ROOT / "synth" / "fault_effects.yaml").read_text())
    return data


def rag_config_hash(fault: str | None) -> tuple[str, str]:
    """(config hash, prompt version) exactly as patient-rag computes them, so replayed
    windows resolve against patient-rag's /configs registry."""
    from patient_rag.config import apply, load_base_config, load_fault

    cfg = load_base_config()
    if fault:
        cfg = apply(cfg, load_fault(fault).overrides)
    return cfg.config_hash(), cfg.prompt_version


def _load_profile() -> tuple[list[float], list[float], dict[str, list[float]]]:
    s = json.loads((DATASETS_DIR / "seasonality.json").read_text())
    personas = list(s["persona_share"])
    hourly = [sum(s["persona_share"][p] * s["hourly"][p][h] for p in personas) for h in range(24)]
    peak = max(hourly)
    load = [h / peak for h in hourly]
    mix = {p: [s["persona_share"][p] * s["hourly"][p][h] for h in range(24)] for p in personas}
    return load, s["weekday"], mix


class Generator:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.fx = load_effects()
        self.load, self.weekday, self.mix = _load_profile()
        self.personas = list(self.mix)

    def _commit(self, r: random.Random) -> str:
        return format(r.getrandbits(160), "040x")

    def events(
        self,
        r: random.Random,
        start: datetime,
        seconds: int,
        *,
        fault: str | None,
        fault_from: int,
        commit: str,
        id_prefix: str,
    ) -> Iterator[dict[str, Any]]:
        b = self.fx["baseline"]
        clean_hash, clean_prompt = rag_config_hash(None)
        f: dict[str, Any] = {}
        faulted_hash, faulted_prompt = clean_hash, clean_prompt
        if fault:
            f = self.fx["faults"][fault]
            faulted_hash, faulted_prompt = rag_config_hash(fault)
        base_top_k = int(self.fx["baseline_config"]["top_k"])
        for i in range(seconds // STEP_S):
            ts = start + timedelta(seconds=i * STEP_S)
            active = fault is not None and i * STEP_S >= fault_from
            e = f if active else {}
            load = self.load[ts.hour] * self.weekday[ts.weekday()]
            weights = [self.mix[p][ts.hour] for p in self.personas]
            persona = r.choices(self.personas, weights)[0]

            retr = b["retrieval_latency_ms"]["median"] * math.exp(
                r.gauss(0, b["retrieval_latency_ms"]["sigma"])
            )
            llm = (b["latency_ms"]["median"] - b["retrieval_latency_ms"]["median"]) * math.exp(
                r.gauss(0, b["latency_ms"]["sigma"])
            )
            gain = 1 + b["load_latency_gain"] * load
            retr *= gain
            llm *= gain
            retr += e.get("retrieval_latency_add_ms", 0) * math.exp(r.gauss(0, 0.2))
            llm *= e.get("latency_mult", 1.0)
            latency = retr + llm

            if "top_score_set" in e:
                top = r.gauss(e["top_score_set"]["mean"], e["top_score_set"]["sd"])
                mean = r.gauss(e["mean_score_set"]["mean"], e["mean_score_set"]["sd"])
            else:
                top = r.gauss(
                    b["top_score"]["mean"] + e.get("top_score_delta", 0), b["top_score"]["sd"]
                )
                mean = r.gauss(
                    b["mean_score"]["mean"] + e.get("mean_score_delta", 0), b["mean_score"]["sd"]
                )
            top = min(max(top, 0.0), 1.0)
            mean = min(max(mean, 0.0), top)
            top_k = int(e.get("hit_count_set", base_top_k))
            hits = top_k if r.random() > 0.03 else r.randrange(1, top_k)

            zero_rate = e.get("citation_zero_rate", b["citation_zero_rate"])
            if r.random() < zero_rate:
                cites = 0
            else:
                lam = b["citation_count"]["mean"] * e.get("citation_mult", 1.0)
                cites = max(1, _poisson(r, lam))
            cites = min(cites, hits)

            grounded: float | None = None
            if r.random() < b["groundedness"]["sample_rate"]:
                delta = e.get("groundedness_delta", 0.0)
                if active and "affected_share" in e and r.random() < e["affected_share"]:
                    delta += e["groundedness_delta_affected"]
                g = r.gauss(b["groundedness"]["mean"] + delta, b["groundedness"]["sd"])
                grounded = round(min(max(g, 1.0), 5.0), 2)

            canary: bool | None = None
            if r.random() < b["canary"]["rate"]:
                persona = "synthetic"
                canary = r.random() < e.get("canary_accuracy", b["canary"]["accuracy"])
            err_rate = e.get("error_rate", b["error_rate"])
            error = r.random() < err_rate
            status = (
                (e.get("status_code", 500) if active and "status_code" in e else 500)
                if error
                else 200
            )
            t_in = max(
                200,
                int(
                    r.gauss(b["tokens_in"]["mean"], b["tokens_in"]["sd"])
                    * e.get("tokens_in_mult", 1.0)
                ),
            )
            t_out = (
                0
                if error
                else max(20, int(r.gauss(b["tokens_out"]["mean"], b["tokens_out"]["sd"])))
            )
            ev = {
                "event_id": f"{id_prefix}-{i:06d}",
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "service": "patient-rag",
                "persona": persona,
                "query_id": None,
                "latency_ms": round(latency, 1),
                "retrieval_latency_ms": round(retr, 1),
                "llm_latency_ms": round(llm, 1),
                "top_score": None if error else round(top, 4),
                "mean_score": None if error else round(mean, 4),
                "hit_count": 0 if error else hits,
                "citation_count": 0 if error else cites,
                "groundedness": None if error else grounded,
                "error": error,
                "status_code": status,
                "tokens_in": t_in,
                "tokens_out": t_out,
                "cost_usd": round(t_in * PRICE_IN + t_out * PRICE_OUT, 7),
                "config_hash": faulted_hash if active else clean_hash,
                "prompt_version": faulted_prompt if active else clean_prompt,
                "git_commit": commit,
                "canary_correct": None if error or canary is None else canary,
                "fault_id": fault if active else None,
            }
            TelemetryEvent.model_validate(ev)
            yield ev


def _poisson(r: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= r.random()
        if p <= limit:
            return k
        k += 1


def generate(seed: int) -> dict[str, Any]:
    g = Generator(seed)
    outputs: dict[str, str] = {}
    counts: dict[str, int] = {}
    r_commit = rng(seed, "telemetry.commits")
    deploys = []
    commit = g._commit(r_commit)
    for day in range(DAYS):
        if day % 3 == 2:  # a code deploy every ~3 days (same RAG config, new commit)
            commit = g._commit(r_commit)
            deploys.append({"ts": (START + timedelta(days=day)).isoformat(), "commit": commit})
        r = rng(seed, f"telemetry.day{day}")
        path = OUT_DIR / "baseline" / f"day-{day + 1:02d}.jsonl.gz"
        rows, digest = write_jsonl_gz(
            path,
            g.events(
                r,
                START + timedelta(days=day),
                86400,
                fault=None,
                fault_from=0,
                commit=commit,
                id_prefix=f"B{day + 1:02d}",
            ),
        )
        outputs[str(path.relative_to(OUT_DIR))] = digest
        counts["baseline_events"] = counts.get("baseline_events", 0) + rows
    segments = []
    for fid in FAULT_IDS:
        for t in range(TRIALS):
            r = rng(seed, f"telemetry.{fid}.t{t}")
            start = SEGMENT_START + timedelta(hours=r.randrange(0, 24 * 6))
            seg_commit = g._commit(r)
            path = OUT_DIR / "faults" / f"{fid}-t{t + 1}.jsonl.gz"
            rows, digest = write_jsonl_gz(
                path,
                g.events(
                    r,
                    start,
                    LEAD_S + FAULT_S,
                    fault=fid,
                    fault_from=LEAD_S,
                    commit=seg_commit,
                    id_prefix=f"{fid}T{t + 1}",
                ),
            )
            outputs[str(path.relative_to(OUT_DIR))] = digest
            counts["fault_events"] = counts.get("fault_events", 0) + rows
            segments.append(
                {
                    "file": str(path.relative_to(OUT_DIR)),
                    "fault_id": fid,
                    "trial": t + 1,
                    "start": start.isoformat(),
                    "fault_onset": (start + timedelta(seconds=LEAD_S)).isoformat(),
                    "end": (start + timedelta(seconds=LEAD_S + FAULT_S)).isoformat(),
                    "lead_in_commit": seg_commit,
                }
            )
    index = {
        "baseline_start": START.isoformat(),
        "days": DAYS,
        "step_seconds": STEP_S,
        "deploys": deploys,
        "segments": segments,
        "baseline_config_hash": rag_config_hash(None)[0],
    }
    idx_path = OUT_DIR / "index.json"
    idx_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    outputs["index.json"] = hashlib.sha256(idx_path.read_bytes()).hexdigest()
    counts["segments"] = len(segments)
    write_manifest(
        OUT_DIR,
        generator="telemetry",
        version=VERSION,
        seed=seed,
        deployment=None,
        counts=counts,
        outputs=outputs,
    )
    return counts


def main() -> None:
    from synth.common import load_synth_config

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=load_synth_config().seed)
    args = ap.parse_args()
    print(f"telemetry: {generate(args.seed)}")


if __name__ == "__main__":
    main()
