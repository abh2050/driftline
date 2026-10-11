# Demo script (about 8 minutes)

Audience: engineers who own a RAG or LLM application. You need the admin role on "Driftline API (dl-dev)" and a browser signed in to the console.

## 0. Before you start (2 minutes)

- Open the console: `azd env get-value CONSOLE_URL`.
- Open the Azure portal → resource group `rg-dl-dev` → **Driftline** workbook.
- Open the GitHub repository's Pull requests tab.
- In the console, on **Demo**, click **Reset patient-rag to baseline**.

## 1. The problem (30 s)

"RAG quality drifts quietly. Someone lowers `top_k`, swaps a prompt or stops an indexer, and answers get worse without any error. Driftline notices, finds the cause, proves a fix and opens the PR, and it never merges anything itself."

## 2. Break it (30 s)

**Demo** → **Replay F3**. "This replays a recorded day of traffic at 1440× in which `top_k` drops from 8 to 2. The detector sees it the way it would see live traffic, in event time."

## 3. Watch it detect (1–2 min)

**Incidents**. Within about 3 minutes a **sev1** `hit_count` row appears, and the status moves through routing, investigating and validating.
- "EWMA plus CUSUM found a sustained drop; the router's D1 decision confirmed it is real, not noise."

## 4. Open the incident (2 min)

- **D2 / D3 / D4 charts:** "Every decision is a scored distribution. Below 0.6 confidence it escalates to a person; under a 0.15 margin both specialists investigate."
- **Root cause:** the retrieval specialist cites the config diff and repository evidence. It reached the repository and Log Analytics through the MCP gateway with its own identity.
- **Sandbox evaluation:** "Before any PR exists, the fix (running config with `top_k: 8`) is evaluated on 40 golden questions against the baseline; the gate is noise-aware."
- **D5 / D6:** accept, then allow, judged before each write.
- **Pull request** link → GitHub: show the PR body (eval table, routing scores, cost, trace link). "Branch protection still needs a human review. Close it without merging."

## 5. A runbook needs a person (1 min)

**Demo** → **Replay F5** (indexer stopped while documents changed). The index specialist proposes a runbook and the workflow pauses. **Approvals** → **Approve runbook**. "The workflow resumes from its Cosmos checkpoint, re-enables the indexer, reindexes and closes the incident, with my name in the audit."

## 6. Safety (1 min)

Open `evals/reports/*-layer3.md`. "We tried to break it: every agent identity tried to merge, push to main, write outside its folder and read the GitHub key, plus 20 poisoned documents and viewers on admin routes. 55 of 55 were blocked, and the report names the layer that stopped each one."

## 7. Observability (1 min)

Workbook → pick the incident → **Trace waterfall**: one trace from the detector through the router, the agents and their tools, the sandbox and the PR. Then **Cost per incident** (about $0.07) and **Eval trends**.

## 8. Ask it (30 s)

**Chat** → paste the incident ID → "Why was this routed to retrieval, and is the fix safe?" The explainer answers from the incident record.

## Reset

**Demo** → **Reset patient-rag to baseline**. Close any demo PRs.
