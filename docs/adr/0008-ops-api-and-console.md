# ADR 0008: ops-api, the console and their sign-in (Phase 8)

- Status: accepted
- Date: 2026-10-10
- Details or deviates from: SPEC 5.1.11, 5.2, 5.3, 6

## 1. One app registration and no client secret

The console signs users in with Container Apps authentication, using the existing **Driftline API** app registration. It does not use a second registration. The user's app roles (viewer, admin) are already defined there, and a token the console requests for its own `user_impersonation` scope has the audience ops-api accepts. `scripts/entra_app.py` exposes that scope, pre-authorises the app for it (so no consent prompt appears), enables ID-token issuance and adds the console's redirect URI.

The code flow needs a client credential. A client secret would be a second secret in the system (spec: the GitHub App key is the only one), so the console's user-assigned managed identity is a **federated identity credential** on the app registration. Container Apps authentication reads the reserved secret name `override-use-mi-fic-assertion-client-id`, whose value is the identity's client ID (not a credential). Microsoft documents this mechanism for App Service as a preview. For Container Apps, the uppercase setting name conflicts with Container Apps secret naming ([azure-container-apps#1693](https://github.com/microsoft/azure-container-apps/issues/1693)), and users report that the lowercase name works. **This has to be confirmed by the first real sign-in (HUMAN_STEPS).** If it fails, the fallback is the browser-only flow below, still without a secret.

The token store is required so that the user's access token reaches the app (`X-MS-TOKEN-AAD-ACCESS-TOKEN`). It uses blob container `tokens`, reached with the same managed identity (`blobContainerUri` + `managedIdentityResourceId`, `authConfigs@2026-07-01`; earlier API versions accept only SAS URLs).

Fallback, if the federated credential is rejected: MSAL in the browser with a single-page-application redirect URI (authorization code with PKCE, public client, no secret). The `/api/ops` proxy would then take the bearer token from the browser instead of the Easy Auth header. Nothing else changes.

## 2. The console calls only ops-api, server side

Pages are client components that call the console's own `/api/ops/*` route. That route forwards to ops-api inside the environment, adds `Authorization: Bearer <user token>` from the Easy Auth header, and streams the response back (SSE feed, chat). The user's token never reaches browser JavaScript, and the browser never reaches Azure services.

The `/demo` page reads the signed-in user's roles from `x-ms-client-principal` on the server. Container Apps authentication sets this header and removes any copy a client sends. Without the admin role, the page renders a refusal. ops-api checks the admin role again on every demo, approval and re-run route. A missing header means no roles.

## 3. ops-api

- Viewer (or admin) for reads, admin for approvals, re-runs and demo controls. Tests cover a viewer on every admin route, and calls without a token. The console's own role check is the second layer, not the only one.
- **Live feed.** One background loop polls Cosmos for incidents whose `_ts` changed (every 3 s), and the `incidents` topic pushes openings and closures immediately. Both fan out to every `GET /stream` client. Polling picks up the workflow's status changes, which are written to Cosmos and not published.
- **Approvals** publish to `approvals` with the user's name as `by`. The orchestrator resumes the paused workflow from its checkpoint (ADR 0007). An approved runbook now **closes** the incident, and `resolution.outcome` records whether automated steps ran (`runbook_executed`) or an operator follows the steps (`runbook_approved`). An acknowledged escalation stays `escalated`.
- **Chat** relays the explainer agent, which runs in the orchestrator because the agent identities live there (`POST /chat`, role `ops.write`, streamed text).
- **Replays from /demo.** ops-api starts the replay job through Azure Resource Manager. It holds **Container Apps Jobs Operator on that one job** and nothing else. An execution template replaces the container's settings, so ops-api passes the job's own environment back with the plan, run ID and speed overridden. The detector needs about 25 minutes of live traffic to detect a live injection (5-minute traffic, 5-window blocks), so the demo's "Replay F#" runs the same fault through its own stream in a few minutes. "Inject F# live" applies it to the running service.
- **Browser traces** go to `POST /v1/traces` (viewer), which forwards them to the collector. The console's fetch instrumentation adds `traceparent` to `/api/ops` calls, so a click joins the trace of the call it causes.

## 4. MCP surface through APIM

ops-api serves the same read and approval operations as MCP tools (`list_incidents`, `get_incident`, `list_approvals`, `respond_to_approval`). APIM API `ops-mcp` validates the caller's Driftline API token, requires viewer or admin, rate-limits and logs per user, and **forwards the user's token**. ops-api then checks the role per tool (`respond_to_approval` needs admin).

This differs from the GitHub and KQL backends, where APIM swaps in its own identity. There, the backends serve agents and APIM is the policy point. Here, the caller is a person, and ops-api must know who approved.

ops-api has external ingress so APIM (outside the environment) can reach it. That does not widen access: every route checks a token, and the direct path grants exactly what APIM's path grants. APIM adds per-user rate limits and gateway logs.

## 5. Other details

- The console is a Next.js 16 standalone build on Node 22. The `package-lock.json` is generated on Linux, because a lock written on macOS omitted a Linux-only optional dependency (`@emnapi/runtime`) and `npm ci` failed in the image build. `npm ci` passes on both platforms with the committed lock.
- Playwright runs all six pages against a mocked ops-api (`tests/mock-ops.mjs`: fixtures, an SSE feed, a streamed chat). Roles come from a test header that the console honours only when `CONSOLE_AUTH_MODE=mock`.
