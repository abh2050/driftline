# ADR 0002: Parametric fault effects for synthetic telemetry until patient-rag exists

- Status: accepted (to be revisited in Phase 3)
- Date: 2026-10-09
- Deviates from: SPEC 3.4 ("generate fault segments by replaying the fault library against the real patient-rag once")

## Context

Phase 1 produces the telemetry replay data, but patient-rag is built in Phase 3, so faults cannot be replayed against it yet. Later phases (detector, replay, layer 2b) need labeled fault segments.

## Decision

- `synth/fault_effects.yaml` holds the baseline distributions and per-fault shifts for F1 to F7. These are initial estimates of how each fault moves latency, retrieval scores, hit count, citations, groundedness, errors and token counts.
- `synth/telemetry.py` produces 14 clean days (120,960 events, one every 10 seconds, with diurnal and weekly load from `evals/datasets/seasonality.json` plus Gaussian noise). It also produces 5 trial segments per fault (2 hours of clean lead-in followed by 4 hours faulted), with every event labeled with `fault_id`.
- In Phase 3, `scripts/inject-fault.py` replays each fault against the deployed patient-rag once, measures the observed shifts, and writes them back into `fault_effects.yaml`. The generator then reruns with the same seed. This ADR then becomes "superseded by calibration", and the recalibration commit records the measured numbers.
- Generator outputs are gzipped JSONL. Because gzip bytes can differ between zlib builds, determinism is checked on the uncompressed content sha256 recorded in `data/telemetry/manifest.telemetry.json`.

## Consequences

- Detector tuning in Phase 5 may move once the effects are calibrated, so detector thresholds stay in `config/detector.yaml`.
- F5 is deliberately subtle in aggregate (only about 12% of queries touch changed documents). Detection relies on the per-query groundedness tail and the fresh-document canary questions in the golden set, not on the mean alone.

## Note on manifests

Several generators share `evals/datasets/`, so each writes `manifest.<generator>.json` rather than a single `manifest.json`.
