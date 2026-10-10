# Copilot Studio governance pack: Driftline operations agent

A design document and reusable enterprise template for publishing a Copilot Studio agent that uses Driftline's MCP server (`ops-mcp` on APIM, see [../mcp-clients.md](../mcp-clients.md)). Building the agent is out of scope for Driftline's definition of done (spec 5.3). This pack is what a platform team needs in order to build and run it safely.

Replace values in `<angle brackets>`. The rest applies as written to any agent that fronts an internal MCP server.

---

## 1. Scope and principles

- **Purpose.** An agent in Microsoft Teams that answers "what is broken and why" from Driftline incidents, and lets on-call admins approve or reject runbooks and acknowledge escalations.
- **Least privilege.** The agent acts as the signed-in user, never as a service account, so Driftline's viewer and admin roles apply unchanged and every approval names a person.
- **One door.** The agent reaches Driftline only through the APIM MCP endpoint. No other connector may carry Driftline data.
- **Everything as code.** Agents ship as solutions through pipelines. Nothing is edited by hand in test or production.

## 2. Environment strategy

| Environment | Type | Who builds | Solutions | Data policy | Driftline endpoint |
|---|---|---|---|---|---|
| `driftline-dev` | Sandbox, Dataverse | makers (section 5) | unmanaged | DLP "Driftline – Dev" | dev APIM (`dl-dev`) |
| `driftline-test` | Sandbox, Dataverse | pipeline only | **managed** | DLP "Driftline – Governed" | test APIM |
| `driftline-prod` | Production, Dataverse, Managed Environment | pipeline only | **managed** | DLP "Driftline – Governed" | prod APIM |

- Turn on **Managed Environments** for test and production: sharing limits, usage insights, solution checker enforcement, IP firewall where licensed.
- Use one security group per environment as the environment security group, so only members can see the environment.
- Use **environment variables** in the solution for the MCP endpoint URL and the OAuth app's client ID, so the same solution moves between environments unchanged.
- Use **connection references** for the MCP connector, created once per environment by an admin and bound at import time.
- The default environment gets the strictest DLP policy (business data group empty, custom connectors blocked), so makers cannot build a Driftline agent outside this path.

## 3. Data loss prevention

Policy name **"Driftline – Governed"**, scoped to `driftline-test` and `driftline-prod`.

| Group | Connectors |
|---|---|
| Business | **Driftline MCP** (the custom MCP connector created by the solution), **Microsoft Teams** |
| Non-business | none |
| Blocked | **every other connector**, including HTTP, HTTP with Microsoft Entra ID, all other custom connectors, and the Bing / public web knowledge sources |

Additional settings:
- **Custom connector URL patterns:** allow only `https://<apim-name>.azure-api.net/ops-mcp/*`; block `*`.
- **Connector endpoint filtering** (where available): the same host pattern.
- **Copilot Studio settings:** disable "Web browsing / generative answers from public websites", disable publishing to channels other than **Teams and Microsoft 365**, and disable unauthenticated access ("No authentication" is not allowed).

"Driftline – Dev" is the same, plus the HTTP connector restricted to the dev APIM host, for maker testing only.

Review the policies quarterly, and whenever Microsoft adds connectors. New connectors default to **Blocked** (set the policy's default group to Blocked).

## 4. Authentication settings

**Agent authentication: "Authenticate with Microsoft", with manual Entra ID configuration.**

1. App registration **"Driftline Copilot Studio (prod)"**, one per environment, owned by the platform team:
   - Redirect URI: `https://token.botframework.com/.auth/web/redirect`.
   - Delegated API permission `api://<tenant>/driftline-api-<env>/user_impersonation`, with admin consent granted.
   - Credential: a **certificate** (preferred) or a secret with at most 6 months' lifetime, kept in the platform team's Key Vault. Never in the solution and never in Driftline.
   - Token configuration: none beyond the defaults. Roles come from Driftline API's own app roles.
2. In Copilot Studio: Settings → Security → Authentication → **Authenticate manually**:
   - Service provider: **Microsoft Entra ID V2**.
   - Client ID: environment variable.
   - Scopes: `openid profile offline_access api://<tenant>/driftline-api-<env>/user_impersonation`.
   - **Require users to sign in:** on.
3. The MCP tool's connection uses **OAuth 2.0 (user)** with the same registration, so each tool call carries the user's token. APIM validates it and ops-api enforces the role: viewers can read, and only admins can call `respond_to_approval`.
4. **Who gets which role.** Assign Driftline roles to groups, not people: `sg-driftline-viewers` → viewer, `sg-driftline-oncall` → admin. Membership in the on-call group is time-bound (Entra PIM for Groups, 8-hour activation, justification required).
5. **Approval confirmation.** The `respond_to_approval` tool is configured with "Ask the user before running", and the topic shows the runbook steps before asking.

## 5. Maker and admin roles

| Role | Who | Can | Cannot |
|---|---|---|---|
| Power Platform admin | platform team (2–3 people) | create environments, DLP policies, Managed Environment settings, connection references | build agents in production |
| Environment admin (test, prod) | platform team | import managed solutions via the pipeline service principal, bind connections | edit solutions in place |
| Environment maker (dev) | Driftline team makers, a named security group | build and test the agent in `driftline-dev`, export unmanaged solutions | share outside dev, create connectors other than Driftline MCP |
| Pipeline service principal | Azure DevOps or GitHub workload identity | export, check, import solutions | interactive sign-in |
| Agent users | `sg-driftline-viewers`, `sg-driftline-oncall` | chat in Teams | change the agent |

Sharing limits (Managed Environments): in production, the agent can be shared only with the two security groups above, and editor sharing is disabled.

## 6. Usage monitoring

| Signal | Where | Alert |
|---|---|---|
| Sessions, users, resolution and escalation rates per topic | Copilot Studio analytics; Managed Environments usage insights (weekly digest to the platform team) | resolution rate < 60% for 7 days |
| Every MCP call: caller object ID, tool, status, trace ID | APIM `GatewayLogs` / `GatewayMCPLogs` in Log Analytics (Driftline workspace) | 403s > 20 in 15 min (role misconfiguration or probing); any 5xx burst |
| Approvals made through the agent | Driftline audit container: `human-response` records carry `by` = the user | approvals outside on-call hours |
| Conversation transcripts | Dataverse `ConversationTranscript` table, retention 30 days | (none; for investigations) |
| Token usage and cost | Copilot Studio message capacity report | 80% of monthly capacity |
| Agent changes | Power Platform admin center audit (Purview) | any change in production outside a pipeline run |

A sample KQL query for the APIM signal:

```kusto
ApiManagementGatewayLogs
| where ApiId == "ops-mcp"
| summarize calls = count(), denied = countif(ResponseCode in (401, 403)) by bin(TimeGenerated, 15m), CallerIpAddress
| where denied > 20
```

## 7. ALM pipeline (Power Platform CLI)

The agent, its topics, the MCP connector, connection references and environment variables live in one solution, `DriftlineOpsAgent`, which is source-controlled unpacked in `copilot/DriftlineOpsAgent/`.

```yaml
# .github/workflows/copilot-agent.yml (template)
name: copilot-agent
on:
  push: { branches: [main], paths: ["copilot/**"] }
  workflow_dispatch:
permissions: { id-token: write, contents: read }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: microsoft/powerplatform-actions/actions-install@v1
      - name: Pack managed solution
        run: pac solution pack --zipfile out/DriftlineOpsAgent_managed.zip --folder copilot/DriftlineOpsAgent --packagetype Managed
      - name: Solution checker
        run: pac solution check --path out/DriftlineOpsAgent_managed.zip --outputDirectory out/check --geo UnitedStates
      - uses: actions/upload-artifact@v4
        with: { name: solution, path: out/ }
  deploy-test:
    needs: build
    environment: driftline-test        # GitHub environment with required reviewers
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: solution, path: out }
      - uses: microsoft/powerplatform-actions/actions-install@v1
      - name: Authenticate (workload identity federation, no secret)
        run: pac auth create --environment ${{ vars.PP_ENV_URL }} --tenant ${{ vars.TENANT_ID }} --applicationId ${{ vars.PP_SP_CLIENT_ID }} --githubFederated
      - name: Import with deployment settings
        run: pac solution import --path out/DriftlineOpsAgent_managed.zip --settings-file copilot/settings/test.json --activate-plugins --publish-changes
  deploy-prod:
    needs: deploy-test
    environment: driftline-prod
    runs-on: ubuntu-latest
    steps: [ "...same as deploy-test with settings/prod.json..." ]
```

- `copilot/settings/<env>.json` binds the environment variables (MCP URL, client ID) and connection references. Generate it once with `pac solution create-settings`.
- Makers export from dev with `pac solution export --managed false` and `pac solution unpack`, then open a pull request. Production changes only through merged PRs.
- Pin action versions to commit SHAs in the real workflow.
- Roll back by re-running the last good workflow run, which re-imports the previous managed version.

## 8. Rollout checklist

Copy this list into the change record for each environment.

**Before the first build**
- [ ] Environments created with the right type; Managed Environments on for test and production
- [ ] Environment security groups set
- [ ] DLP policies "Driftline – Governed" and "Driftline – Dev" applied, and the default environment locked down
- [ ] App registration per environment, with redirect URI, delegated `user_impersonation`, admin consent, and a certificate in Key Vault
- [ ] Security groups `sg-driftline-viewers` and `sg-driftline-oncall` assigned to the Driftline API roles; PIM for on-call
- [ ] Pipeline service principal with workload identity federation, added as application user (System Customizer) in test and prod

**Before production**
- [ ] Solution checker passes with no high-severity findings
- [ ] Test sign-in as a viewer: incidents readable, `respond_to_approval` refused
- [ ] Test sign-in as an admin: approval resumes the workflow; the audit record names the user
- [ ] No-auth and public web knowledge disabled; channels limited to Teams and Microsoft 365
- [ ] APIM alert rules for 401/403 bursts and 5xx are live
- [ ] Usage insights digest goes to the platform team
- [ ] Sharing limited to the two security groups
- [ ] Rollback rehearsed by re-importing the previous managed version in test
- [ ] Owners recorded: business owner, technical owner, platform contact

**After go-live (first 30 days)**
- [ ] Weekly review of analytics, denied calls and approvals made through the agent
- [ ] Rotate the app registration credential before 6 months
- [ ] Quarterly DLP review; re-certify group membership
