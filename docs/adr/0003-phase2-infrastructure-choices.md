# ADR 0003: Phase 2 infrastructure choices

- Status: accepted
- Date: 2026-10-09
- Deviates from: SPEC 5.1.9, 5.1.12, 5.4.5, 9 (`infra/modules/entra-app`), 10

## 1. Native OTLP ingestion instead of the Azure Monitor exporter

The spec names the collector's Azure Monitor exporter. Azure Monitor now ingests OTLP natively through a data collection endpoint (DCE) and data collection rule (DCR). The collector uses the standard `otlphttp` exporter, authenticated by the `azure_auth` extension with the collector's managed identity, which holds Monitoring Metrics Publisher on the DCR.

- No connection string or instrumentation key exists anywhere. Application Insights has `DisableLocalAuth: true`.
- Traces and logs land in Log Analytics (OTel tables) and are linked to Application Insights through the DCR's `applicationInsights` reference. Metrics go to an Azure Monitor workspace.
- The collector is contrib 0.162.0, which is past the `azure_auth` server-side advisory GHSA-pjv4-3c63-699f (fixed after 0.150.0). Inbound OTLP is not authenticated with `azure_auth`. Ingress is internal, or IP-restricted while a developer runs `scripts/test_span.py`.
- Tail sampling and span metrics run in the collector as specified. Span metrics are computed before sampling so rates are unbiased.

## 2. AI Search Free tier (cost decision)

Estimated monthly spend with Search Basic is $175–205, which is over the $150 budget. The owner chose the Free tier (50 MB, 3 indexes, no SLA). That is enough for the 330-document corpus and for one `sbx-*` sandbox index at a time. Entra RBAC works on Free; firewall rules do not, and we don't need them. Switching to Basic is the `SEARCH_SKU` azd variable.

## 3. APIM Developer tier

Developer is the lowest-cost tier that supports MCP servers (the Learn availability list includes Developer and Basic v2; Consumption is not listed). It has no SLA, which is acceptable for a portfolio system. `APIM_SKU` switches it.

## 4. Entra app registration from an azd hook, not Bicep

The Bicep Microsoft Graph extension must be restored from MCR. On this macOS build, the Bicep CLI (.NET 10) crashes during the TLS handshake (`AccessViolationException` in X509 handling), so the extension cannot be restored locally. `scripts/entra_app.py` runs as the `preprovision` and `postprovision` hooks and does the same work idempotently through `az rest` against Microsoft Graph:

- It creates the "Driftline API (<env>)" app with human roles (`viewer`, `admin`) and application roles (`router.invoke`, `patientrag.*`, `ops.write`, `agent.*`), sets the identifier URI `api://<tenant>/driftline-api-<env>`, and creates a service principal with assignment required.
- After provisioning, it grants application roles to service identities (spec section 10) and gives the person running azd the `admin` role.

## 5. Identity and role deviations from section 10

- `sandbox-runner` also gets Cognitive Services OpenAI User, because it builds patient-rag in process and needs embeddings and chat. It also gets `patientrag.query`.
- `ops-api` also gets Service Bus Data Receiver, so it can stream incident updates over SSE from its own `incidents` subscription.
- `orchestrator` holds all `agent.*` app roles, because the agents run inside the orchestrator process. APIM maps each agent's calls to its role through a per-agent token audience/claim (Phase 6).
- An `approvals` topic carries workflow-resume events from ops-api to the orchestrator.
- The person running azd gets data-plane roles (Foundry, Search, Cosmos, Service Bus, Storage, Key Vault, Log Analytics) for local development and smoke tests.

## 6. Other details

- Container Apps jobs (traffic-gen, replay, sandbox-runner) cannot use Dapr sidecars, so they use the Service Bus SDK directly with the same W3C trace-context properties. Long-running apps use Dapr pubsub. Each app has its own pubsub component scoped to it and bound to its identity.
- The `audit` container uses a time-based immutability policy with protected append writes. Retention is 1 day in dev so `azd down` can clean up (Phase 10 acceptance). Raise `auditRetentionDays` for real use.
- Key Vault soft delete is 7 days with purge protection off, so `azd down --purge` works in dev.
- Model deployment capacities live in `infra/main.parameters.json`. gpt-5-mini starts at 200K TPM because 250K is still held by the temporary deployment in the portal project used for Phase 1 synthesis.
