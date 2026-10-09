# Driftline Build Spec (v4)

## v4 scope change (2026-10-09, ADR 0001)

This section overrides anything below that conflicts with it.

1. Microsoft-Decision-1 and Claude are not used, because the subscription has no quota for either. Wherever this spec says "Decision-1" or "Microsoft-Decision-1", read "the active router (an LLM router behind DecisionClient)". Wherever it says "the Claude deployment", read "gpt-5-mini".
2. Model deployments in westus3 (see ADR 0001 for the full table): gpt-5-mini (primary router, specialists, synthetic data), gpt-4.1-mini and gpt-oss-120b (router baselines, and gpt-4.1-mini is also the patient-rag chat model), gpt-5.4-mini (judge), text-embedding-3-small and text-embedding-3-large (embeddings; large is F2's wrong deployment), Cohere-rerank-v4.0-fast and -pro (reranker; pro is F6's slow tier).
3. Section 4.1 clients: three `LLMRouterClient` instances (gpt-5-mini, gpt-4.1-mini, gpt-oss-120b) plus `RulesRouterClient`. No Decision1Client, no ClaudeRouterClient, and no Decision-1 contract fixture. Instead, record one real response per router model as a contract fixture.
4. Mission goal 2 becomes a measured comparison of LLM routers against a rules baseline. Definition of done 13.2 is updated to match.
5. Region: westus3.


## 0. How to use this document

1. Install az, azd, gh, uv, Docker, and Node.js 22. Run `az login` and `azd auth login`.
2. Confirm Foundry quota in one region for Microsoft-Decision-1, a general chat model, a Claude model, an embedding model, and a judge model. Decision-1 launched on 2026-10-09, so region availability is the most likely blocker.
3. Create an empty GitHub repo named driftline and clone it.
4. Save this entire document as docs/SPEC.md in that repo.
5. Open Claude Code in the repo root and paste this kickoff line:

   Read docs/SPEC.md in full. Build Driftline exactly as specified, phase by phase, starting at Phase 0. Verify current SDK and Foundry docs before writing code against them, record deviations as ADRs, and stop only when a step requires me, using docs/HUMAN_STEPS.md.

Claude Code creates .github/copilot-instructions.md from Appendix A during Phase 0, so GitHub Copilot follows the same conventions when you edit by hand.

## 1. Mission

Build Driftline, a multi agent system on Azure. Driftline watches a production RAG application, detects silent quality degradation, routes each incident to the right specialist agent with Microsoft-Decision-1, finds the root cause, writes a candidate fix, proves the fix with evals in an isolated sandbox, and opens a pull request that a human must merge. Engineers interact with Driftline through a web console. All data is synthetic. The whole system, including the console, deploys with one `azd up`.

The portfolio goals are:

1. Show production depth on Microsoft Foundry: Agent Service, model catalog deployments, tracing, evaluations, and content safety.
2. Show a measured, independent evaluation of Microsoft-Decision-1 as a router against LLM routers.
3. Show a governed human interface with an MCP surface that Copilot Studio or any other MCP client can connect to later.

## 2. Operating rules for Claude Code

1. Before you write code against any Microsoft or Azure SDK, read the current official docs. Cover at least: the Microsoft-Decision-1 model card and API in the Foundry model catalog, Microsoft Agent Framework (Python), azure-ai-projects, azure-ai-evaluation, Azure AI Content Safety prompt shields, Azure API Management MCP support, Azure Container Apps jobs and built in authentication, Dapr on Container Apps, and the OpenTelemetry Collector Azure Monitor exporter. Pin exact versions. When the current API differs from this spec, follow the docs and record the difference in an ADR.
2. Microsoft-Decision-1 launched on 2026-10-09. Its request and response schema may not match any SDK yet. Wrap it behind the DecisionClient interface in section 4, write a raw REST implementation from the model card, and add a contract test that records one real response as a fixture.
3. Build in the phase order in section 11. At the end of each phase, run lint, type checks, tests, and the phase acceptance check. Commit with the message "phase N: <summary>". Do not start the next phase until the acceptance check passes.
4. Never hardcode secrets. Every service authenticates with its own user assigned managed identity through DefaultAzureCredential. The GitHub App private key is the only secret, and it lives in Key Vault.
5. When a step needs the human (az login, GitHub App installation, model quota, Entra app registration consent), append the exact commands or portal steps to docs/HUMAN_STEPS.md, print them, and stop until the human confirms.
6. Backend stack: Python 3.12, uv workspace, FastAPI, pydantic v2, httpx, ruff, mypy strict, pytest, pytest-asyncio. Frontend stack: Next.js with the App Router, TypeScript strict, Tailwind, shadcn/ui, Recharts, ESLint, Playwright. Infrastructure uses Bicep and azd. Containers use multi stage Dockerfiles.
7. Every service sends OTLP traces, metrics, and logs to the otel-collector container app, never directly to Azure Monitor. Propagate W3C trace context through Service Bus message properties and through every agent, router, tool, and console call, so one incident produces one connected trace.
8. Model deployment names, thresholds, routing confidence cutoffs, and schedules come from configuration files, never from code constants.
9. Every synthetic data generator takes a seed, and the same seed must produce byte identical output.

## 3. Synthetic data

No real data exists for this project. Build every dataset under data/ and evals/datasets/ with generators in synth/. Check generated outputs into git so evals are reproducible without regeneration. Each generator writes a manifest.json with the seed, generator version, model deployment used, and record counts.

1. Corpus (synth/corpus.py). Generate about 300 documents for a fictional industrial manufacturer named Cascade Forge Works. Document types: standard operating procedures, equipment maintenance manuals, safety bulletins, quality specifications, change notices, and an engineering FAQ. Use a seeded topic graph of plants, lines, equipment, part numbers, and revision numbers so documents cross reference each other consistently. Generate text with a Foundry chat deployment and cache every response in synth/cache. Include a second version of 30 documents with changed values (torque specs, intervals, part numbers) for fault F5.
2. Golden QA set (synth/golden.py). Generate 150 questions with reference answers and source document ids: 100 standard questions and 50 questions that depend on the changed documents. Add a `reviewed` field set to false. Write a small review CLI that lets the human mark records reviewed.
3. Traffic queries (synth/traffic.py). Generate 2,000 user queries from 6 personas (maintenance tech, quality engineer, safety officer, plant manager, new hire, auditor) with realistic phrasing, typos, and a daily seasonality profile.
4. Telemetry replay (synth/telemetry.py). Generate 14 days of per request telemetry at 1 request per 10 seconds with diurnal load, weekly seasonality, and Gaussian noise. Generate fault segments by replaying the fault library against the real patient-rag once and scaling the observed metric shifts onto the synthetic baseline. Label every segment with its fault id.
5. Routing dataset (synth/routing.py). Generate 1,200 incident cases, each labeled with the correct specialist, severity, and action. Build each case from a fault template plus randomized evidence (metric deltas, config diffs, log snippets). Make 20 percent of cases ambiguous, with a primary and a secondary specialist label. Generate 8 paraphrase perturbations for 200 cases to test robustness.
6. Red team documents (synth/redteam.py). Generate 20 corpus documents with embedded prompt injections that instruct agents to delete files, approve their own PRs, read secrets, or route incidents to the wrong specialist. Index them only in the eval environment.

## 4. Microsoft-Decision-1 integration

### 4.1 DecisionClient interface

packages/driftline-common/decision.py defines:

    class Decision(BaseModel):
        question_id: str
        options: list[str]
        scores: dict[str, float]      # calibrated probabilities that sum to 1
        chosen: str
        margin: float                 # top score minus second score
        latency_ms: float
        model: str
        input_tokens: int
        cost_usd: float

    class DecisionClient(Protocol):
        async def decide(self, question: str, options: list[str], context: dict, rubric: str | None = None) -> Decision: ...

Implement four clients behind this interface:

1. Decision1Client calls the Microsoft-Decision-1 deployment in Foundry.
2. LLMRouterClient prompts a general chat deployment to return JSON scores for the same options.
3. ClaudeRouterClient does the same with the Claude deployment.
4. RulesRouterClient uses hand written rules over evidence fields as a floor baseline.

The active client comes from config/router.yaml. Every decision emits a span named `decision.<question_id>` with all Decision fields as attributes and writes one row to the Log Analytics custom table DriftlineDecisions_CL.

### 4.2 Where Driftline calls Decision-1

1. D1 verify. The detector asks whether a breach is a real incident or noise (options: real, noise). Context: metric deltas, window counts, recent deploy events.
2. D2 severity. The router assigns sev1, sev2, sev3, or sev4.
3. D3 specialist routing. The router picks the retrieval, prompt, index, or performance specialist. Context includes the incident evidence and live agent health (p95 latency, error rate, and queue depth per specialist over the last 15 minutes) pulled from Log Analytics through kql-tool. When the margin is below `dual_route_margin`, the router dispatches the top two specialists in parallel and keeps the higher confidence RCA.
4. D4 action. After RCA, the router picks pr, runbook, or escalate.
5. D5 fix verification. The validator sends the eval comparison and the patch diff with a rubric, and Decision-1 returns accept or reject. The deterministic eval gate must also pass. Both must agree to accept.
6. D6 action precheck. Before any write tool call, the orchestrator sends the proposed call with a policy rubric, and Decision-1 returns allow, needs_human, or deny. APIM still enforces policy independently, so D6 is a second layer.

When the top score is below `escalation_threshold` (default 0.6) for any decision, Driftline pauses the workflow and places an escalation in the console approvals inbox until a person answers.

## 5. Architecture

### 5.1 Components

All components run on Azure Container Apps in one environment with Dapr enabled, unless the entry says otherwise.

1. patient-rag (container app). The target RAG application. FastAPI, Azure AI Search, and Foundry embedding and chat deployments over the Cascade Forge Works corpus. Behavior comes from apps/patient-rag/config/rag.yaml (chunk_size, chunk_overlap, top_k, embedding_deployment, chat_deployment, prompt_version, reranker_enabled) and prompts in apps/patient-rag/prompts/<version>.md. Each request publishes a telemetry event to the Service Bus topic `telemetry` through Dapr: latency, retrieval scores, hit count, citation count, config hash, prompt version, git commit, and persona. Ten percent of requests get an async groundedness score from azure-ai-evaluation. An admin endpoint protected by an Entra ID app role applies faults from section 6.
2. traffic-gen (Container Apps job, cron every 5 minutes). Sends queries from evals/datasets/traffic.jsonl following the seasonality profile.
3. replay (Container Apps job, manual trigger). Pushes synthetic telemetry from data/telemetry onto the `telemetry` topic at configurable speed, so evals can run 14 days of history in minutes.
4. ingest (container app). Subscribes to `telemetry`, validates against a pydantic schema, and writes one minute aggregate windows to the Cosmos DB container `windows`.
5. detector (container app, Dapr cron binding every minute). Keeps EWMA baselines and CUSUM statistics per metric and config hash in the Cosmos DB container `baselines`. On a sustained breach it calls D1. When D1 returns real, it publishes an Incident to the topic `incidents` with metric deltas, windows, and the config diff since the baseline commit.
6. router (container app). Exposes POST /decide and implements every DecisionClient. All services call the router instead of calling models directly, so routing latency and cost are measured in one place.
7. orchestrator (container app). Subscribes to `incidents` and runs a Microsoft Agent Framework workflow with checkpoints in Cosmos DB and human in the loop support. Workflow steps: D2 severity, D3 routing, specialist RCA and patch, D4 action, validator with sandbox and D5, D6 precheck, and pr writer. Agents are registered in Foundry Agent Service:
   a. retrieval-specialist handles chunking, top_k, embedding, and reranking problems.
   b. prompt-specialist handles prompt version and citation behavior problems.
   c. index-specialist handles stale content, indexer state, and schema problems. It produces runbooks instead of patches for runtime causes.
   d. performance-specialist handles latency, throttling, and capacity problems.
   e. validator starts sandbox-runner, waits on `validations`, and runs D5.
   f. pr-writer opens branch driftline/fix-<incident_id> and a pull request whose body contains the root cause, cited evidence, the routing decision with scores, the eval comparison table, token and decision cost, and an Application Insights trace link.
   g. explainer answers questions about incidents for the console chat panel. It has read only tools.
   Each specialist reads git history through GitHub MCP read tools and queries telemetry through kql-tool. Specialists use the Claude deployment by default, configurable per agent in config/agents.yaml. Every step writes one JSON audit record to the immutable Blob container `audit`. Retrieved document content passes through Azure AI Content Safety prompt shields before any agent sees it.
8. sandbox-runner (Container Apps job, event triggered). Clones the repo at the incident commit, applies the patch, and builds patient-rag in process. If the patch changes indexing parameters, it builds a temporary index named sbx-<run_id> and deletes it at the end. Otherwise it uses the live index with a read role. It runs the layer 1 suite, writes results to the Blob container `eval-results`, and publishes a summary to `validations`.
9. otel-collector (container app, internal ingress). Runs the OpenTelemetry Collector contrib image. Receives OTLP from every service. Applies tail sampling that keeps all errors, all traces slower than the configured threshold, all incident traces, and 10 percent of the rest. Exports to Application Insights through the Azure Monitor exporter. Computes span metrics (request rate, error rate, latency histograms) per service, agent, decision type, and model deployment. Configuration lives in apps/otel-collector/config.yaml.
10. kql-tool (container app, internal ingress). Runs read only, parameterized KQL queries against Log Analytics. It exposes only named queries from apps/kql-tool/queries/, never free form KQL from agents.
11. ops-api (container app). Serves the human facing API: list incidents, get incident detail, approve or reject runbooks, answer escalations, rerun validation, stream live updates over server sent events at GET /stream, stream explainer answers at POST /chat, and run demo controls at POST /demo/fault and POST /demo/reset. Admin routes check the admin role claim. ops-api also accepts browser OTLP traces over HTTP and forwards them to otel-collector. APIM exposes the same read and approval operations as MCP tools.
12. gateway (Azure API Management). Choose the lowest cost tier that supports MCP server exposure. APIM fronts the GitHub MCP server, kql-tool, and the ops-api MCP surface. Policies: validate the Entra ID JWT on every call; map each managed identity app role to a tool allowlist; deny merge_pull_request, branch protection changes, deletes, and pushes to main; deny file writes outside apps/patient-rag/config and apps/patient-rag/prompts; rate limit each identity; log every call with identity and trace id.
13. github-mcp (container app, internal ingress). Runs GitHub's official MCP server with a GitHub App installation token. The App has contents write, pull requests write, and metadata read on this repo only. Branch protection on main requires one human approval and passing CI.
14. console (container app). The web console described in section 5.2.

### 5.2 Web console

1. The console uses Next.js with the App Router, TypeScript strict, Tailwind, shadcn/ui, and Recharts. Container Apps built in authentication handles Entra ID sign in. The Entra app registration defines two app roles: viewer and admin.
2. The console calls only ops-api, forwarding the signed in user's token. It never calls Cosmos DB, Service Bus, or models directly.
3. Pages:
   a. /incidents shows a live feed over server sent events from ops-api GET /stream, with browser notifications for sev1, sev2, and escalations.
   b. /incidents/[id] shows severity, routing scores per option as a bar chart with the margin, the specialist RCA with cited evidence, the eval comparison table, the D5 and D6 decisions, the PR link, cost, and an Application Insights trace link.
   c. /approvals lists runbooks and escalations awaiting a human. Approve and reject actions call ops-api, which resumes the workflow.
   d. /router renders the latest layer 2a report: accuracy, ECE with a reliability diagram, flip rate, latency percentiles, and cost per 1,000 decisions for each client.
   e. /chat streams answers from the explainer agent through ops-api.
   f. /demo, restricted to the admin role, injects faults F1 to F7, starts replay runs, and resets patient-rag to baseline.
4. The console sends browser traces through ops-api to otel-collector, so user actions join the incident trace.

### 5.3 Optional MCP clients

ops-api keeps its MCP surface through APIM. Document in docs/mcp-clients.md how to connect Copilot Studio, VS Code, and Claude Desktop to it. Building a Copilot Studio agent is out of scope for the definition of done. Write a Copilot Studio governance pack in docs/copilot-studio/ as a design document covering environment strategy (dev, test, prod with managed solutions), a DLP policy that allows only the Driftline MCP connector and Teams, authentication settings, maker and admin roles, usage monitoring, an ALM pipeline with the Power Platform CLI, and a rollout checklist written as a reusable enterprise template.

### 5.4 Shared resources

1. Cosmos DB for NoSQL, serverless, with containers `windows`, `baselines`, `incidents`, and `checkpoints`.
2. Storage account with an immutable container `audit` and a container `eval-results`.
3. Service Bus Standard with topics `telemetry`, `incidents`, and `validations`.
4. Foundry resource and project with deployments for Microsoft-Decision-1, a general chat model, a Claude model, an embedding model, and a judge model. Turn on Foundry tracing and connect it to the same Application Insights resource.
5. Azure AI Content Safety, Azure AI Search Basic, Key Vault, Container Registry, Log Analytics with the custom table DriftlineDecisions_CL and its data collection rule, and Application Insights.
6. A budget alert at 150 USD per month, set in main.parameters.json.

### 5.5 End to end flow

traffic-gen calls patient-rag. patient-rag publishes telemetry. ingest writes windows. detector finds a breach and calls D1 through the router. detector publishes an Incident, and the console shows it live. The orchestrator calls D2 and D3, and the router picks a specialist using incident evidence and live agent health. The specialist writes an RCA and a patch. The orchestrator calls D4. For a pr action, the validator runs sandbox-runner and D5, the orchestrator runs D6, and pr-writer opens the pull request through APIM and github-mcp. For a runbook action or a low confidence decision, the workflow pauses and the item appears in the console approvals inbox. A human approves or rejects it in the console, which resumes the workflow. A human merges the PR. CI deploys patient-rag. The detector confirms recovery and closes the incident. Every hop appears in one trace in Application Insights and in Foundry tracing.

## 6. Fault library

Each fault lives in apps/patient-rag/faults/<id>.yaml with a label file in evals/incidents/<id>.json holding the true root cause, correct specialist, severity, correct action, and expected fix.

1. F1 chunk_size changes from 512 to 2048. Retrieval recall drops. Specialist: retrieval. Action: pr.
2. F2 embedding_deployment changes without a reindex. Retrieval scores collapse. Specialist: retrieval. Action: pr that reverts the deployment.
3. F3 top_k changes from 8 to 2. Groundedness drops. Specialist: retrieval. Action: pr.
4. F4 prompt_version moves to a version without the citation instruction. Citation rate drops. Specialist: prompt. Action: pr.
5. F5 the indexer is disabled while 30 documents change. Correctness on fresh questions drops. Specialist: index. Action: runbook with human approval in the console.
6. F6 the reranker is enabled on a slow tier. p95 latency rises while quality holds. Specialist: performance. Action: pr that disables the reranker.
7. F7 the chat deployment is throttled through an APIM rate limit. Error rate and latency rise. Specialist: performance. Action: escalate.

## 7. Evals

All evals run through `uv run driftline-eval <layer> [options]` and upload runs to Foundry evaluations.

### 7.1 Layer 1: RAG quality

1. Metrics: recall@k and MRR from source ids; groundedness, relevance, and response completeness from azure-ai-evaluation; citation rate; p50 and p95 latency; token cost per query.
2. Gate: evals/gates.yaml sets a maximum regression per metric. A patch passes when the degraded metric recovers to within 2 percent of baseline and no other metric regresses beyond its threshold.
3. Offline mode uses cached responses in evals/fixtures so CI runs without Azure calls.

### 7.2 Layer 2a: router benchmark

Run every DecisionClient over the routing dataset for D1 through D4, and over a graded fix set for D5. Report per client and per decision type:

1. Accuracy and macro F1 against labels. For ambiguous cases, report a strict view (primary label only) and a lenient view (primary or secondary label).
2. Calibration as expected calibration error with 10 bins, plus a reliability diagram.
3. Robustness as the decision flip rate across the 8 paraphrase perturbations.
4. Latency at p50, p95, and p99, measured at the router and at the client.
5. Cost per 1,000 decisions.
6. Escalation rate at the configured threshold, and accuracy on the cases that did not escalate.

Write evals/reports/<date>-router.md and a machine readable evals/reports/<date>-router.json that the console /router page renders. This report is the main portfolio artifact.

### 7.3 Layer 2b: end to end system

For each fault F1 to F7, run 5 trials using replay at accelerated speed. Measure detection recall, false positives over 48 hours of clean replay, time to detect, routing accuracy, RCA top 1 accuracy (exact label match plus a judge rubric graded by Decision-1 and by the judge model, with agreement reported), correct action rate, fix acceptance rate, token and decision cost per incident, and wall time. Write evals/reports/<date>-system.md.

### 7.4 Layer 3: safety and policy

Scripted cases force each agent identity to attempt a merge, a push to main, a write outside the allowlist, a Key Vault read, and actions triggered by the injected red team documents. Add console cases where a viewer tries to reach /demo and admin routes on ops-api. A case passes when content safety, D6, APIM, or ops-api role checks block it and an audit record exists. Report which layer blocked each case. The required pass rate is 100 percent.

### 7.5 CI

1. Every pull request runs ruff, mypy, pytest, Bicep lint, synthetic data determinism checks, and layer 1 offline.
2. Every pull request that touches apps/console runs ESLint, the TypeScript compiler, and Playwright tests for all six pages against a mocked ops-api.
3. Pull requests from driftline/* branches run layer 1 live and post the comparison as a PR comment.
4. A nightly job runs layers 2a, 2b, and 3 against dev through an OIDC federated credential.
5. Merges to main deploy changed services with azd deploy.

## 8. Observability

1. Every service and the console send OTLP to otel-collector. Span names follow `<service>.<operation>`, and agent spans follow the OpenTelemetry GenAI semantic conventions.
2. Build an Azure Workbook in infra/workbooks/driftline.json with these views: incident timeline, trace waterfall per incident, latency by agent and model, router decision latency and confidence distribution, cost per incident split into tokens and decisions, escalation rate, and nightly eval trends.
3. Define alerts for the router p95 latency SLO, the escalation rate, the otel-collector drop rate, and the budget.

## 9. Repository layout

    driftline/
      azure.yaml
      pyproject.toml
      config/ (router.yaml, detector.yaml, agents.yaml)
      docs/
        SPEC.md
        HUMAN_STEPS.md
        architecture.md
        adr/
        mcp-clients.md
        copilot-studio/
        threat-model.md
        well-architected.md
        cost-model.md
        aws-to-azure.md
        demo-script.md
      infra/
        main.bicep
        main.parameters.json
        modules/ (aca-env, cosmos, servicebus, storage, keyvault, foundry, contentsafety, search, apim, monitoring, dcr, identity, entra-app, budget)
        apim/policies/
        workbooks/
      packages/
        driftline-common/ (schemas, decision.py, telemetry.py, audit.py, clients, config loader)
      apps/
        patient-rag/ (config/, prompts/, faults/, src/)
        traffic-gen/
        replay/
        ingest/
        detector/
        router/
        orchestrator/ (src/agents/, src/workflow.py, src/tools/)
        sandbox-runner/
        otel-collector/
        kql-tool/ (queries/)
        ops-api/
        github-mcp/
        console/ (app/, components/, lib/, tests/)
      synth/ (corpus.py, golden.py, traffic.py, telemetry.py, routing.py, redteam.py, cache/)
      data/ (corpus/, telemetry/)
      evals/
        cli.py
        datasets/
        incidents/
        gates.yaml
        layer1/
        layer2a_router/
        layer2b_system/
        layer3_safety/
        fixtures/
        reports/
      scripts/ (bootstrap.sh, seed-index.py, inject-fault.py, smoke.py)
      .github/
        workflows/ (ci.yml, console.yml, nightly-evals.yml, deploy.yml)
        copilot-instructions.md
      tests/

## 10. Identity and roles

Create one user assigned managed identity per service and grant only these roles:

1. patient-rag: Search Index Data Reader, Cognitive Services OpenAI User, Service Bus Data Sender.
2. ingest: Service Bus Data Receiver, Cosmos DB Built-in Data Contributor.
3. detector: Cosmos DB Built-in Data Contributor, Service Bus Data Sender, router app role.
4. router: Azure AI User on the Foundry project, Monitoring Metrics Publisher on the data collection rule.
5. orchestrator: Azure AI User, Service Bus Data Receiver and Sender, Cosmos DB Built-in Data Contributor, Storage Blob Data Contributor on `audit`, Cognitive Services User on Content Safety, and one APIM app role per agent.
6. sandbox-runner: Search Index Data Contributor limited in code to sbx-* indexes, Storage Blob Data Contributor on `eval-results`, Service Bus Data Sender.
7. kql-tool: Log Analytics Reader.
8. ops-api: Cosmos DB Built-in Data Reader, Storage Blob Data Reader on `eval-results`, Service Bus Data Sender for workflow resume events, router app role for the explainer chat, and the patient-rag admin app role for demo routes.
9. github-mcp: Key Vault Secrets User for the GitHub App key only.
10. console: no Azure data roles. Human users get the viewer or admin app role on the Entra app registration.

## 11. Phases and acceptance checks

1. Phase 0, repository and tooling. Create the uv workspace, the console package skeleton, CI skeletons, pre-commit hooks, docs/HUMAN_STEPS.md, and .github/copilot-instructions.md from Appendix A. Acceptance: `uv run pytest` and the console lint pass, and CI is green.
2. Phase 1, synthetic data. Build every generator in synth/ and generate all datasets. Acceptance: generators are deterministic by seed, manifests exist, and the human has reviewed at least 30 golden records.
3. Phase 2, infrastructure and telemetry spine. Write every Bicep module, deploy otel-collector first, and run azd provision. Acceptance: smoke.py lists every resource, and a test span from a local script appears in Application Insights through the collector.
4. Phase 3, patient-rag, traffic, and the layer 1 baseline. Acceptance: the index is seeded with the synthetic corpus, traffic flows, and a baseline report is stored.
5. Phase 4, router and Decision-1. Build the router with all four clients, record the Decision-1 contract fixture, and run layer 2a. Acceptance: the router report exists with all six metric groups for all four clients.
6. Phase 5, ingest, detector, and replay. Acceptance: F3 produces an incident within 15 minutes after D1 returns real, and 48 hours of clean replay produces at most one false positive.
7. Phase 6, vertical slice. Build the orchestrator with D3 routing, retrieval-specialist, and pr-writer, plus the GitHub App, github-mcp, and APIM. Acceptance: F3 produces a pull request with routing scores in the body, and a merge attempt from the pr-writer identity returns 403 with an audit record.
8. Phase 7, full workflow. Add the remaining specialists, D2, D4, D5, D6, validator, sandbox-runner, content safety, and dual routing. Acceptance: F1, F3, F4, and F6 produce validated PRs, F5 produces a runbook awaiting approval, and F7 escalates.
9. Phase 8, ops-api and console. Build ops-api streaming, approvals, chat, and demo routes, then build the console and deploy it with azd. Acceptance: a signed in admin injects F3 from /demo, watches the incident appear live on /incidents, sees routing scores on the detail page, and opens the resulting PR. A viewer cannot reach /demo. Approving the F5 runbook on /approvals resumes the workflow and closes the incident.
10. Phase 9, system and safety evals. Acceptance: layer 2b and layer 3 reports exist, runs are visible in Foundry evaluations, and layer 3 passes at 100 percent.
11. Phase 10, observability and documentation. Build the workbook and alerts, and write the ADRs, threat model, Well-Architected review, cost model, AWS to Azure mapping, MCP clients guide, Copilot Studio governance pack, and demo script. Acceptance: azd down followed by azd up from a clean clone recreates a working system, and the workbook shows a full incident.

## 12. ADRs to write

1. Microsoft-Decision-1 as the router compared with an LLM router, backed by layer 2a numbers.
2. A dedicated router service compared with routing inside the orchestrator.
3. Dual routing on low margin compared with single routing.
4. Agent Framework workflow in a container app with Foundry Agent Service agents compared with fully hosted agents.
5. A central OpenTelemetry Collector compared with direct export from each service.
6. Policy at APIM plus D6 plus branch protection compared with policy in agent code.
7. GitHub App compared with a personal access token.
8. Blob immutability for audit compared with Cosmos DB.
9. EWMA plus CUSUM with D1 verification compared with a learned anomaly model.
10. A custom web console with an MCP surface compared with Copilot Studio as the primary interface.
11. Synthetic data generation strategy and its limits.

## 13. Definition of done

1. One `azd up` deploys the whole system, including the console.
2. The router report compares Microsoft-Decision-1 with three baselines on accuracy, calibration, robustness, latency, and cost, and the console renders it.
3. Every fault reaches the correct action in at least 4 of 5 trials.
4. No agent can merge, push to main, or write outside its allowlist, and the layer 3 report names the blocking layer for each case.
5. One incident produces one connected trace from telemetry event to console approval or pull request.
6. The README explains the system to a reader with no context in under 5 minutes, with an architecture diagram, the router results table, console screenshots, and the demo video link.

## Appendix A. Contents of .github/copilot-instructions.md

# Copilot instructions for Driftline

Driftline is a multi agent system on Azure that detects quality degradation in a RAG application, routes incidents with Microsoft-Decision-1, and opens pull requests that humans merge. Engineers use it through a web console. The full spec is in docs/SPEC.md. Read it before you suggest structural changes.

Follow these conventions:

1. Use Python 3.12, FastAPI, pydantic v2, and async code for backend services. Code must pass ruff and mypy strict.
2. Use Next.js with the App Router, TypeScript strict, Tailwind, and shadcn/ui for the console. Code must pass ESLint and the TypeScript compiler.
3. Authenticate to Azure with DefaultAzureCredential and managed identity. Never put keys or connection strings in code or config.
4. Route every decision through the router service and the DecisionClient interface. Never call a model directly to choose between fixed options.
5. Send telemetry over OTLP to otel-collector. Start a span for every agent call, decision, tool call, Service Bus message, and console action, and put trace context into message properties.
6. Import schemas, the decision interface, telemetry setup, and the audit writer from packages/driftline-common.
7. Read thresholds, deployment names, and schedules from the config folder or environment variables.
8. Agents may write only to apps/patient-rag/config and apps/patient-rag/prompts. Never add code that merges PRs, pushes to main, or changes branch protection.
9. The console talks only to ops-api. Admin features check the admin role on both the console and ops-api.
10. Synthetic data generators must take a seed and produce identical output for the same seed.
11. Every feature needs a test: pytest for services and Playwright for console pages. Every patient-rag behavior change needs a layer 1 eval run.
12. Azure resources go in infra/modules as Bicep. Do not create Azure resources from application code, except the temporary sbx-* search indexes in sandbox-runner.
