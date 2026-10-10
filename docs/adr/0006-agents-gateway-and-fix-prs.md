# ADR 0006: Agents, the MCP gateway and fix pull requests (Phase 6)

- Status: accepted
- Date: 2026-10-10
- Details or deviates from: SPEC 5.1.7, 5.1.10, 5.1.12, 5.1.13, 10

## 1. One managed identity per agent

The spec gives the orchestrator "one APIM app role per agent". If one identity held every role, APIM could not tell the agents apart. Instead, there are seven user-assigned identities (`agent-retrieval`, `agent-prompt`, `agent-index`, `agent-performance`, `agent-validator`, `agent-prwriter`, `agent-explainer`). Each holds exactly one `agent.*` app role, and all of them are attached to the orchestrator app. A tool call carries the calling agent's own token, so APIM maps it to that agent's tool allowlist, and the audit log names the agent. The orchestrator's own identity keeps only `router.invoke` plus its data roles.

## 2. github-mcp is GitHub's official server behind a token proxy

GitHub's MCP server supports GitHub App authentication only in stdio mode. In HTTP mode every request must carry a GitHub token. The `github-mcp` container app therefore runs two containers:

- `ghcr.io/github/github-mcp-server:v2.0.2 http` with toolsets `repos,pull_requests`;
- a small proxy (`apps/github-mcp`) that admits only callers holding the `mcp.gateway` role (APIM's managed identity). It mints installation tokens from the App key in Key Vault, scoped to this repository and to `contents:write`, `pull_requests:write`, whatever the App itself is granted. It caches the token until 5 minutes before expiry and streams requests to the server.

The github-mcp identity can read exactly one secret (`github-app-private-key`, a secret-scoped role assignment). The backend accepts traffic only from APIM's public IP, and it also verifies APIM's token.

## 3. APIM MCP: what it took to make pass-through work

- An MCP API with only `serviceUrl` makes APIM act as its own, empty MCP server: it answers `initialize` itself and returns 404 for every tool. Pass-through needs a `backends` resource referenced by `backendId` (`infra/modules/apim-mcp.bicep`).
- APIM's MCP gateway returns 500 for protocol version `2025-11-25`, the default of the mcp Python SDK 1.30. The orchestrator pins `2025-06-18` (`apps/orchestrator/src/orchestrator/tools.py`).
- APIM policy values must not be empty strings ("Expression value is invalid. The value field is required"). Every computed value has a non-empty default (`none`).
- The MCP client library turns an HTTP 403 on `tools/call` into a 60-second timeout. Safety probes therefore speak raw MCP over HTTP and record the HTTP status (`orchestrator.probe`). Agents still see the denial as a failed tool call.
- `GatewayLogs` and `GatewayMCPLogs` go to Log Analytics (`ApiManagementGatewayLogs`). They arrive with a delay of about 30 minutes after first configuration.

## 4. Foundry Agent Service registration, Agent Framework execution

Every agent (model and instructions) is registered as a versioned prompt agent in the Foundry project at startup. A new version is created only when the definition hash changes. Runs execute in the orchestrator through Microsoft Agent Framework (`Agent` on `FoundryChatClient`) with local MCP tools, so every tool call carries the agent's own identity through APIM. Fully hosted agents would make tool calls with Foundry's identity and lose per-agent policy (spec ADR list item 4).

The workspace pins `mcp==1.30.0`, because agent-framework-core 1.21 requires `mcp<2`. kql-tool uses the 1.x `FastMCP` server.

## 5. Fix pull requests restore and pin configuration

Faults are applied to the running patient-rag, so `apps/patient-rag/config/rag.yaml` in the repository usually already holds the good value. A PR that only restated it would be empty. A fix PR therefore sets the keys the specialist named, pins each one with an `# pinned by Driftline <incident>` comment, and increments `config_revision`. Merging deploys a new patient-rag revision, which replaces the faulted runtime state, and the detector then observes recovery and closes the incident. Specialists return structured output (`RcaResult`) with config changes as `{key, value}` pairs, because strict structured outputs reject open-ended objects. The YAML is edited line by line by deterministic code (`patching.py`), never by the model.

## 6. Other details

- The otel-collector's short app name does not resolve inside the Container Apps environment (it was created with external ingress and an additional port mapping), while other apps' names do. Services use the collector's internal FQDN over HTTPS. Until this was fixed, service spans were silently dropped.
- Branch protection on `main` requires one approving review and the `python` and `bicep` checks, and blocks force pushes and deletions. Admins are not enforced, so the owner can still push build commits. Driftline's PRs are authored by the App, so the owner can approve them.
- kql-tool exposes `list_queries` and `run_query`. Parameters are typed (`int(lo..hi)`, `enum(...)`, `hex32`) and validated before substitution, so a caller cannot inject KQL.
- Adding `config_revision` changed every patient-rag config hash. The synthetic telemetry was regenerated so replayed windows resolve against `/configs`.
