# ADR 0001: Use an LLM router and only models with existing quota

- Status: accepted
- Date: 2026-10-09
- Changes: SPEC v3 → v4 (see "v4 scope change" at the top of docs/SPEC.md)

## Context

On 2026-10-09 the subscription (37dc8b49-…, Pay-As-You-Go) has 0 quota in every region for Microsoft-Decision-1 and for every Claude model. A test deployment fails with `InsufficientQuota`. The OpenAI Decisions API would need a second external secret and puts the router outside Azure. The owner chose to drop dedicated decision models and build only on models that can be deployed today.

## Decision

1. No dedicated decision model. Every D1 to D6 decision is made by an **LLM router**: a chat model prompted to return JSON scores over the fixed options. The scores are normalized to probabilities that sum to 1. The `Decision` model, the `DecisionClient` protocol, the router service, spans, DriftlineDecisions_CL rows and the escalation threshold all stay as specified.
2. Models in **westus3**, all verified on 2026-10-09 with a test deployment and an Entra-authenticated inference call (HTTP 200), then deleted:

   | Role | Model (version) | SKU | Quota (K TPM) | Retires |
   |---|---|---|---|---|
   | Primary router, specialists, explainer, synthetic data | gpt-5-mini (2025-08-07) | GlobalStandard | 500 | 2027-02-09 |
   | Router baseline: model without a reasoning step | gpt-4.1-mini (2025-04-14) | GlobalStandard | 200 | 2027-04-14 (Legacy) |
   | Router baseline: open-weight model | gpt-oss-120b (1) | GlobalStandard | 5000 | none |
   | patient-rag chat | gpt-4.1-mini | GlobalStandard | shared | |
   | Judge | gpt-5.4-mini (2026-03-17) | DataZoneStandard | 200 | 2027-09-21 |
   | Embeddings (baseline) | text-embedding-3-small (1) | GlobalStandard | 1000 | 2028-02-09 |
   | Embeddings (F2 wrong deployment) | text-embedding-3-large (1) | Standard | 350 | 2028-02-09 |
   | Reranker (baseline) | Cohere-rerank-v4.0-fast (1) | GlobalStandard | 20 | |
   | Reranker (F6 slow tier) | Cohere-rerank-v4.0-pro (1) | GlobalStandard | 20 | |

   Rejected: o4-mini (deprecating, so new deployments are refused), MAI-Thinking-1 (Preview, retires 2026-11-04), Phi-4 and grok (20–50K TPM, which would make a ~6,400-decision benchmark take hours), and Decision-1, Claude, gpt-5.4 and gpt-5.5 (0 quota).

3. The four DecisionClients become `LLMRouterClient` instances configured with gpt-5-mini, gpt-4.1-mini and gpt-oss-120b, plus `RulesRouterClient` as the floor. Layer 2a benchmarks all four. The one that wins on accuracy, calibration and cost becomes `active` in `config/router.yaml`. That choice is recorded in the spec's ADR 1 ("LLM router choice, backed by layer 2a numbers").
4. Calibration of LLM scores is a measured result, not an assumption. Layer 2a reports ECE, and if raw scores are poorly calibrated the router may apply temperature scaling fitted on a held-out split (recorded in its own ADR).
5. Quota requests for Decision-1 and Claude are no longer on the critical path. If they are approved later, adding a client is a new `DecisionClient` implementation plus a config entry.

## Consequences

- Portfolio goal 2 becomes "a measured comparison of LLM routers (a reasoning model, a model without a reasoning step, and an open-weight model) against a rules baseline, with calibration and robustness".
- Everything stays on Azure with managed identity, and the GitHub App key remains the only secret.
- Decision latency and cost will be higher than a dedicated decision model's. The router report measures both.
