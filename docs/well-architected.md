# Well-Architected review

A review of the dev deployment against the five pillars of the Azure Well-Architected Framework. Each pillar lists what is done, what is deliberately not done (dev, one-time build), and what a production version would add.

## Reliability

| Done | Gap (accepted for dev) | Production |
|---|---|---|
| The incident workflow checkpoints every step in Cosmos and resumes after a restart. Tested: an F7 escalation resumed from its checkpoint after an orchestrator redeploy (ADR 0007). | The orchestrator's sandbox wait is an in-memory future; a restart mid-validation times out to an escalation. | Persist pending validations and re-subscribe on start. |
| Service Bus decouples every hop. The sandbox job renews its message lock, and the eval runners retry `az`. | One region (westus3), no zone redundancy. Single-replica patient-rag and detector hold state in memory. | Zone-redundant environment, Cosmos multi-region writes, detector state already in Cosmos so replicas can scale out. |
| Fail-safe defaults: content-filtered decisions escalate, prompt shields fail closed, D6 needs_human blocks. | APIM Developer tier has no SLA. | APIM StandardV2 or Premium for the MCP gateway. |

## Security

See [threat-model.md](threat-model.md).
- Managed identities everywhere; no keys or connection strings. The only secret is the GitHub App key in Key Vault, readable by one identity.
- Defence in depth for writes: D6, APIM policy, branch protection. Layer 3 passes 55/55 cases and names the blocking layer for each.
- Entra sign-in for people (no client secret), OIDC for CI, write-once audit.
- Production additions: private endpoints and a VNet-integrated environment (dev uses public endpoints with Entra-only auth), Defender for Cloud, APIM in a VNet, and customer-managed keys if required.

## Cost optimization

See [cost-model.md](cost-model.md). Measured: about $0.07 per incident end to end; month-to-date spend is mostly model calls from evals.
- Serverless or consumption tiers wherever possible: Cosmos serverless, Container Apps consumption with scale-to-zero for jobs and kql-tool, Search Free.
- The collector drops FastAPI's internal spans (about 1 GB a day of Log Analytics ingestion) and tail-samples everything except errors, slow traces and incident traces.
- Evals run in dry-run for PRs, with 3 trials instead of 5; the nightly schedule is off; traffic-gen runs hourly.
- The fixed cost is APIM Developer (about $50 a month) and Log Analytics; `azd down` removes everything.

## Operational excellence

- Everything is infrastructure as code (`azd up`): Bicep plus hooks for Entra objects and grants.
- CI: lint, types, tests, Bicep build, synthetic-data determinism, layer 1 offline; console lint, types and Playwright.
- Observability: one trace per incident across services, GenAI semantic conventions on agent spans, a workbook (timeline, waterfall, agents, router, cost, evals), and alerts for router p95, escalation rate, collector drops and budget.
- Evals at four layers, recorded in Foundry evaluations and in `DriftlineEvals_CL` for trends.
- Gap: merges to `main` do not deploy automatically (spec 7.5.5). The deploy workflow is a stub, because the build is torn down after evaluation. A production version runs `azd deploy` for changed services on merge, through the same OIDC identity.

## Performance efficiency

- Measured: router p50 about 2.4 s and p95 about 5.2 s (gpt-5-mini, D1); median detection about 10 event-minutes after onset; median incident wall time about 6 minutes, most of it the sandbox evaluation.
- The sandbox evaluates 40 stratified golden questions, not the full set, and builds a temporary index only when chunking changes.
- Accelerated replay (1440×) lets one machine-hour of evals cover days of traffic.
- Gap: the explainer and specialists run tool loops serially; dual routing is the only parallelism. Specialists could fan out their KQL and file reads.
