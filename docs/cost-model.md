# Cost model

Prices are list prices for westus3 in October 2026 and are estimates; `config/pricing.yaml` holds the model prices Driftline uses to cost its own decisions and tokens.

## Measured

- **Month-to-date (dev, after building and evaluating everything):** about $31, of which model calls are $28.9 (93%), APIM $1.3, Container Apps $0.8, everything else under $0.2. Most model spend came from building and evaluating: synthetic data, the golden set, layer 1 judging, the router benchmark (about 12,000 decisions per client), and 21 layer 2b trials.
- **Per incident** (layer 2b, 21 incidents): **$0.067** on average:
  - $0.013 agent tokens (specialists and the PR writer);
  - $0.003 router decisions (D2 to D6, about 6 per incident);
  - about $0.05 sandbox answers (40 golden questions through patient-rag). The sandbox's LLM judge, about 120 calls per validation, is not metered per incident and adds roughly $0.03 to $0.05.
- **Runbook and escalation incidents** cost about $0.02, because they skip the sandbox.

## Fixed monthly cost (dev, always on)

| Resource | Tier | Approx. per month |
|---|---|---|
| API Management | Developer | $50 |
| Container Apps | Consumption; about 9 always-on apps at minimal size (most idle) | $10–25 |
| Log Analytics | Pay as you go, after the collector noise filter | $5–15 |
| Container Registry | Basic | $5 |
| Cosmos DB | Serverless | under $2 |
| Service Bus | Standard | $10 |
| AI Search | Free | $0 |
| Key Vault, Storage, Content Safety (S0, low volume) | | under $2 |
| **Total** | | **about $85–110** |

The budget alert is set at $150 a month (spec 5.4.6). Teardown with `azd down --purge` returns this to zero.

## Variable cost drivers

| Driver | Unit cost | Notes |
|---|---|---|
| patient-rag query | about $0.0018 | gpt-4.1-mini answer plus embedding; 10% sampled for groundedness by gpt-5.4-mini |
| Live traffic (traffic-gen) | about $0.35 a day hourly (8 queries) | It was $4 a day at every 5 minutes; changed in Phase 9 |
| Router decision | $0.0003–0.0007 | gpt-5-mini; about 300 tokens in, 200 out |
| Incident (pr) | about $0.07 | see above |
| Layer 2a router benchmark | $0.72 per 1,000 decisions (gpt-5-mini) | a full run for three model clients is about $15; the Phase 10 re-run of D3/D4 cost about $7 |
| Layer 2b (21 trials) | about $2 | plus judging |
| Layer 3 | under $0.10 | mostly router calls |

## Levers already pulled

- Dry-run PRs and 3 trials per fault in evals.
- Nightly schedule off.
- traffic-gen hourly.
- Collector noise filter.
- Serverless Cosmos and Search Free.
- Scale-to-zero for jobs and kql-tool.

## Levers for a longer-lived deployment

- Move the router to gpt-oss-120b. In layer 2a it was about 3× cheaper per decision with similar accuracy, but it escalates more often (ADR 0005).
- Cache sandbox validations per (config hash, change).
- Lower the groundedness sampling rate.
- Share one APIM instance across environments.
