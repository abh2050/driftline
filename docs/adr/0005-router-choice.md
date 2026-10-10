# ADR 0005: gpt-5-mini as the active router, backed by layer 2a

- Status: accepted
- Date: 2026-10-10
- Spec ADR list item 1 ("Microsoft-Decision-1 as the router compared with an LLM router"), rewritten for the LLM-router scope of ADR 0001
- Evidence: `evals/reports/2026-10-10-router.md` / `.json` (full dataset, 40,160 decisions, 0 unrecovered errors)

## Results (strict accuracy / ECE / $ per 1,000 decisions)

| Decision | gpt-5-mini | gpt-4.1-mini | gpt-oss-120b | rules |
|---|---|---|---|---|
| D1 real vs noise | 0.955 / 0.048 / 0.47 | 0.957 / 0.061 / 0.29 | 0.959 / 0.112 / 0.18 | 1.000 / 0.300 / 0 |
| D2 severity | 0.822 / 0.076 / 0.82 | 0.517 / 0.393 / 0.36 | **0.862** / 0.044 / 0.26 | 0.387 / 0.313 / 0 |
| D3 specialist (lenient) | 0.731 (0.812) / 0.086 | 0.693 (0.777) / 0.149 | 0.738 (0.830) / 0.074 | **0.878 (0.983)** / 0.178 |
| D4 action | 0.850 / 0.053 | 0.860 / 0.079 | 0.892 / 0.098 | **0.928** / 0.228 |
| D5 fix verification | **0.945** / 0.055 | 0.685 / 0.286 | 0.900 / 0.098 | 0.845 / 0.145 |
| Invalid outputs | **0%** everywhere | 0% | 1–8% | n/a |
| Model p95 latency | 3.5–5.0 s | 2.7–3.1 s | 1.9–3.5 s | ~0 |

## Decision

`config/router.yaml` keeps `active: gpt-5-mini`.

- It is the only LLM that never produced an invalid output. Invalid outputs become uniform scores and escalations, which page a human.
- It has the best D5 accuracy (0.945). D5 guards what reaches a pull request, so a false accept is the costliest mistake in the system.
- It is well calibrated (ECE ≤ 0.086 on every decision type), so the escalation threshold (0.6) behaves predictably. Its escalation rate is under 1%.
- gpt-oss-120b is the cheapest and fastest LLM and slightly more accurate on D2–D4, but invalid outputs (8% on D1) and a high D2 paraphrase flip rate (0.224) make it less dependable. It stays configured as the comparison baseline. Switching is one line in `config/router.yaml`.
- gpt-4.1-mini (no reasoning step) is poorly calibrated on D2 and D5 (ECE 0.39 and 0.29) and is not suitable as the router.

## The rules floor beats the LLMs on D1, D3 and D4. Read this as a property of the synthetic dataset.

The routing cases are generated from fault templates whose config keys map directly to a specialist and an action (`synth/routing.py`), and real incidents always span at least 3 windows. A keyword rule can recover that template structure. Real incidents are rarely this clean, and the rules client fails exactly where judgement is needed: D2 severity (0.387) and ambiguous evidence.

Follow-ups:
- Layer 2b (end to end on replayed faults, Phase 9) measures routing on detector-produced evidence, which is less templated, and reports routing accuracy per fault.
- A hybrid design ("rules first, router when no rule fires or the rules disagree with the config diff") is a candidate optimization for cost and latency. It is not adopted here because it would hide model behaviour the portfolio is meant to measure.

## Consequences

- Router decisions cost about $0.5–0.9 per 1,000 and take 3–5 s at p95, which is acceptable for incident workflows (minutes, not milliseconds).
- The router report is regenerated nightly (Phase 9) and rendered by the console `/router` page (Phase 8).
