# ADR 0004: Router service and detection pipeline design (Phases 4 and 5)

- Status: accepted
- Date: 2026-10-10
- Deviates from or details: SPEC 3.4, 4.1, 5.1.2–5.1.6, 7.2

## Router (Phase 4)

1. **LLM clients ask for JSON scores and normalize them.** Unknown keys are dropped, missing options get a small floor, and the result is renormalized. A model that wraps its answer (`{"final": {...}}` or a JSON-encoded string inside `final`, seen with gpt-oss-120b) is unwrapped. An output with no usable scores gets one retry, and the retry's latency and tokens count toward that decision. If it is still invalid, the distribution becomes uniform, which always escalates. Layer 2a reports the invalid-output rate per client.
2. **Agent health is D3 context only.** Spec 4.2 gives live agent health to D3 routing. In the first benchmark sample, models read the agents' own error rate as the RAG application's and inflated severity, so D1, D2 and D4 receive incident evidence without `agent_health`.
3. **Rules floor.** The rules client uses simple keyword and threshold heuristics, not the label generator's thresholds (that would score 100% by construction). Its scores are a fixed 0.7 on the chosen option.
4. **Latency is reported twice.** "Model" is the client's measurement of the model call, which the router service records. "Client" is wall time around `decide()` in the benchmark process. The benchmark runs the clients in process, because the router has internal ingress only. The nightly job (Phase 9) runs it inside the environment against the deployed router to add network latency.
5. **Decision rows.** The router writes `DriftlineDecisions_CL` rows through the logs-ingestion DCR with its managed identity, batched in the background so a sink failure never fails a decision. Rows appear about 5 minutes after the decision.
6. **Contract fixtures.** One real request and response per LLM client is stored in `evals/fixtures/router/<client>.json` (ADR 0001 replaces the Decision-1 contract test with these).

## Detection (Phase 5)

1. **Event-time windows.** Ingest closes one-minute windows by event-time watermark (latest event minus 90 s), so a 1440x replay aggregates exactly like live traffic. Open windows live in memory, so ingest runs a single replica.
2. **Streams.** Every event carries `stream` ("live", or "replay-<run id>"). Windows, baselines and incidents are partitioned by stream, so replays never contaminate the live baseline or each other.
3. **Non-overlapping 5-minute blocks.** The detector pools five one-minute windows into each observation and evaluates non-overlapping blocks. Overlapping pools are autocorrelated and made CUSUM fire about five times too often (424 false alarms on 14 clean days in the first sweep).
4. **Per-metric guards.** Besides the CUSUM threshold, an alarm needs a minimum absolute and relative change, and every metric has a standard-deviation floor (`config/detector.yaml`). p95 latency is the median of window p95s, and its baseline adapts faster (alpha 0.2) to follow diurnal load. The tuned detector has 0 false alarms on 14 clean synthetic days, detects F1–F4, F6 and F7 within 9 minutes of onset, and detects F5 within about 40 minutes (`apps/detector/tests`).
5. **Canary questions for F5.** A stale index (F5) leaves answers grounded in what was retrieved, so groundedness cannot reveal it. traffic-gen sends golden questions whose expected value follows patient-rag's current source version, and patient-rag records `canary_correct`. The detector watches canary accuracy. Synthetic telemetry now models canaries (20% of events) and the F5 accuracy drop.
6. **Config diff from a registry.** Telemetry carries only a config hash. patient-rag serves `/configs` (baseline plus every fault config, keyed by hash), and the detector diffs the baseline hash against the current one. Synthetic telemetry uses the same real hashes, so replays resolve too.
7. **Replay publishes CloudEvents with the Service Bus SDK.** Container Apps jobs cannot run Dapr sidecars (ADR 0003), so replay writes Dapr-compatible CloudEvents (`traceparent` included) that ingest consumes through its normal Dapr subscription.
8. **Live traffic volume is a cost decision.** At the original 24 queries per 5 minutes, live traffic alone would cost about $225 a month. traffic-gen now sends 8 queries and 2 canaries per run at peak. A live demo (Phase 8) triggers a short burst of traffic so the live detector sees enough samples.

## Consequences

- The router is the single place to compare models, cost and latency, and every decision is auditable in Log Analytics.
- The detector is deliberately conservative. Faults need a sustained and material change, and D1 is the second filter. F5 detection is slower because it depends on sparse canaries.
- The F5 and synthetic fault effect sizes are still estimates (ADR 0002). Layer 1 runs per fault (Phase 7) measure real effects and recalibrate `synth/fault_effects.yaml`.
