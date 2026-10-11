# ADR 0010: The design decisions the spec asks for (§12)

- Status: accepted
- Date: 2026-10-11
- Details: SPEC §12. Each entry states the choice, the alternative, why, and where the evidence is. Earlier ADRs hold the details.

## 1. The router: LLM routers compared with a rules floor (Decision-1 unavailable)

Microsoft-Decision-1 was not available to this subscription (ADR 0001). The router is therefore a generic "scored JSON decision" client over Foundry models, benchmarked against a hand-written rules floor (layer 2a, ADR 0005). gpt-5-mini is the active router: best calibration and accuracy among the models at an acceptable cost. gpt-oss-120b is cheaper but escalates more often.

After the Phase 7 rubric changes, gpt-5-mini routes (D3) at 89.3%, ahead of the rules floor at 87.8%, with ECE 0.09 against the floor's 0.18. The floor still edges D4 (92.8% vs 91.0%), because it shares the synthetic data's structure. It cannot produce calibrated confidence, it is brittle to unseen evidence, and it can't read an RCA (D4/D5/D6). The live system uses the LLM router. Evidence: `evals/reports/2026-10-11-router.md`, layer 2b routing at 95%.

## 2. A dedicated router service compared with routing inside the orchestrator

A separate service: the detector needs D1 too, every decision lands in one table with the same schema (`DriftlineDecisions_CL`), clients are swappable per request for benchmarks, and the escalation threshold, the content-filter fallback (ADR 0009) and cost accounting live in one place. The cost is one extra hop (about 10 ms, inside the environment) and one more container.

## 3. Dual routing on a low margin compared with single routing

When D3's top two specialists are within 0.15, both investigate concurrently and the higher-confidence RCA is used (ADR 0007). This roughly doubles the agent cost on ambiguous incidents only. In layer 2b no trial needed it (margins were high), so it is a safety net, not a crutch. Single routing would have to escalate ambiguous cases to a person instead.

## 4. Agent Framework in a container app, with Foundry agents, compared with fully hosted agents

Agents are registered as Foundry prompt agents, versioned by definition hash, but run in the orchestrator through Microsoft Agent Framework. Each tool call then carries the agent's own managed identity to APIM, which is what lets APIM enforce per-agent allowlists. With fully hosted agents, every call would come from Foundry's identity (ADR 0006). The workflow (checkpoints, human gate, resume) is also simpler in-process.

## 5. A central OpenTelemetry Collector compared with direct export

One collector does tail sampling (keeps errors, slow traces and every incident trace), span metrics before sampling, the noise filter (about 1 GB a day saved, ADR 0009 / Phase 10), and authentication to Azure Monitor with one identity. Direct export would mean exporter config and credentials in every service, and no tail sampling. The cost is a single point to monitor, covered by the collector drop-rate alert.

## 6. Policy at APIM plus D6 plus branch protection, compared with policy in agent code

Three independent layers:
- **D6** is a model judgement that also understands intent (for example a PR body that leaks secrets).
- **APIM** is deterministic, outside the agents' process, and can't be talked out of a rule.
- **Branch protection** requires a human for `main`.

Policy in agent code could be bypassed by a prompt-injected agent, or by a bug in the same process it constrains. Layer 3 shows which layer stopped each of 55 attacks; prompt shields alone caught 10% of injected documents (ADR 0007).

## 7. A GitHub App compared with a personal access token

A GitHub App installed on one repository has its own identity in the audit log ("driftline[bot]"). Its installation tokens are minted per hour and narrowed to `contents:write` and `pull_requests:write`. Its key lives in Key Vault, readable by one identity. A PAT is tied to a person, long-lived and broad, and can't be narrowed per request. The App can't approve its own PRs under branch protection (ADR 0006).

## 8. Blob immutability for audit compared with Cosmos DB

Audit records are write-once blobs under a time-based immutability policy, chained by the previous record's SHA-256. Nobody, including the writers, can change or delete them during retention. Cosmos documents can be updated or deleted by anyone with write access, so tamper evidence would depend on application code. Blobs are also cheaper for append-only data that is rarely read.

## 9. EWMA plus CUSUM with D1 verification compared with a learned anomaly model

EWMA baselines with one-sided CUSUM per metric are explainable ("hit_count 7.9 → 2.0, CUSUM 57"), need no training data, and adapt slowly. Per-metric guards (minimum relative and absolute change, a floor on the standard deviation) and D1 cut false positives. Result: 0 false positives in 48 h of clean replay and 100% detection (layer 2b). A learned model would need labelled incidents we do not have, and its alarms are harder to explain to the agents that must act on them. A config change log keeps the diff right for gradual faults (ADR 0007).

## 10. A custom console with an MCP surface compared with Copilot Studio as the primary interface

The console shows things a chat surface can't: routing score charts with margins, the eval comparison table, live SSE updates, approvals with runbook steps, the router benchmark with a reliability diagram. The same read and approval operations are MCP tools through APIM, so Copilot Studio, VS Code or Claude Desktop can use them (docs/mcp-clients.md), and the governance pack covers a Copilot Studio rollout (docs/copilot-studio/). Copilot Studio as the primary interface would hide the decision evidence that makes the system trustworthy.

## 11. Synthetic data strategy and its limits

The corpus, golden set, routing cases and telemetry are generated deterministically (seeded; `scripts/check-synth-determinism.sh` in CI). Telemetry is parametric: fault effects are calibrated from layer 1 runs of each fault, not invented (ADR 0002). Replays then exercise the real detector and workflow.

Limits:
- **Synthetic regularities.** Synthetic incidents are cleaner than real ones; the rules floor's high accuracy shows how regular the routing data is.
- **One corpus domain.**
- **Golden set unreviewed.** The human review of the golden set was deferred, so layer 1 scores are unreviewed (HUMAN_STEPS).
- **Evidence leakage.** Agents could read fault definitions until Phase 7 (ADR 0007). The RCA results in layer 2b were measured after that was closed.
