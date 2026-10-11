# Threat model

Scope: the deployed Driftline system (dev environment) and the GitHub repository it changes. The method is STRIDE per trust boundary. "Evidence" points to tests and eval results that show a control working. Layer 3 results are in `evals/reports/*-layer3.md`.

## Assets

1. The repository's `main` branch and the deployments it drives.
2. The GitHub App private key, the only stored secret.
3. Audit records: who decided what, and why.
4. Incident data and model decisions.
5. Azure spend (models, APIM, compute).

## Trust boundaries

| # | Boundary | Crossed by |
|---|---|---|
| B1 | Untrusted content → agents | Document text, repository files, tool results read by specialists |
| B2 | Agents → tools | MCP calls from agent identities through APIM |
| B3 | Orchestrator → GitHub | PR writer through APIM and the GitHub App proxy |
| B4 | People → Driftline | Console (Entra sign-in), ops-api, MCP clients |
| B5 | CI → Azure | GitHub Actions OIDC |
| B6 | Services → data | Managed identities to Cosmos, Storage, Service Bus, Key Vault, Foundry |

## Threats and mitigations

| Threat (STRIDE) | Boundary | Mitigation | Evidence / residual risk |
|---|---|---|---|
| **Prompt injection** in a retrieved document makes an agent delete files, merge or push to main (T, E) | B1→B2 | Prompt shields on every tool result (fail closed); D6 before every write; APIM denies merges, deletes, branch protection changes, pushes to main and writes outside `apps/patient-rag/{config,prompts}`; branch protection requires a human review | Layer 3: all 20 red team documents blocked. Shields caught 10%, so D6 and APIM carry the load. Residual: an injection could still steer an RCA's *content*; the sandbox gate and human review catch a wrong fix |
| **Injected routing** pushes a decision to a chosen option (T) | B1 | Router context is data; content filter; a target-based test per misroute document | Layer 3: no misroute document reached its target. Severity shifts slightly when *any* document is added (shown by a benign control) |
| **Agent over-privilege**: a read-only specialist writes (E) | B2 | One identity per agent, one role each; per-role allowlists in APIM | Layer 3: all 7 identities refused merge, push to main and write outside (28/28) |
| **Agents read the answers** (fault definitions, eval keys) and fake a diagnosis (T, I) | B2 | APIM denies reads of `faults/`, `evals/`, `synth/`, `data/`, `docs/`, `scripts/`; `search_code` is not offered | Probe `read-faults` gets 403. Residual: `get_commit` diffs can show those paths (ADR 0007) |
| **Secret theft**: an agent or injection reads the GitHub key (I) | B6 | Key Vault RBAC: only github-mcp's identity can read that one secret; agents have no secret-reading tool | Layer 3: Key Vault read refused for every agent identity (azure-rbac) |
| **Token leak via PR body** (I) | B3 | Agents never hold tokens or environment variables; D6 judges every PR body | Layer 3: 3 leak cases denied by D6 |
| **Forged approvals** or a viewer using admin routes (S, E) | B4 | Entra sign-in; admin role checked in the console and again in ops-api; MCP `respond_to_approval` needs admin; the approver's name comes from the token, not the request | Layer 3: 7/7 viewer cases refused (ops-api), each audited; Playwright test of a viewer on /demo |
| **Spoofed MCP caller** at a backend (S) | B2 | Backends accept only APIM's managed identity (`mcp.gateway`), and their ingress allows only APIM's IP | Unit tests; IP restrictions in Bicep |
| **Repudiation**: "the agent did it" (R) | all | Write-once, hash-chained audit blobs (time-based immutability); every decision in `DriftlineDecisions_CL`, every span with the incident id | Audit chain written per step; layer 3 requires an audit record per case |
| **CI credential theft** (S, E) | B5 | OIDC federated credential limited to `main` of this repository; roles scoped to two jobs, readers and Foundry | No secret exists to steal |
| **Runaway cost**: a loop of incidents or a model storm (DoS) | all | Router escalation, sandbox job max 5 executions, APIM rate limits per identity, budget alert, cost per incident tracked | $150 budget alert; workbook cost view |
| **Content-filter refusal** crashing decisions (DoS) | B1 | A refused prompt yields the conservative option and escalates | Router unit test; layer 3 |
| **Tampering with telemetry** to hide or fake drift (T) | app → SB | Service Bus with Entra-only auth; only patient-rag and replay may send | Residual: a compromised patient-rag could forge its own telemetry |

## Out of scope / accepted

- A compromised Azure subscription owner.
- Model provider behaviour beyond content filtering.
- Availability across regions: dev runs in one region (westus3), without zone redundancy.
