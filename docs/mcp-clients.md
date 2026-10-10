# Connecting MCP clients to Driftline

Driftline's operations (incidents, approvals) are available as an MCP server through Azure API Management:

| | |
|---|---|
| Endpoint | `https://dl-dev-apim-rf5tbsjvwdzcs.azure-api.net/ops-mcp/mcp` (`azd env get-value OPS_MCP_URL`) |
| Transport | Streamable HTTP, protocol version `2025-06-18` |
| Tools | `list_incidents`, `get_incident`, `list_approvals`, `respond_to_approval` |
| Auth | Entra ID bearer token for the Driftline API, scope `api://<tenant>/driftline-api-dl-dev/user_impersonation` |
| Roles | **viewer** reads; **admin** may also call `respond_to_approval` |

APIM validates the token, requires viewer or admin, rate-limits each user (60 calls a minute) and logs every call with the caller and trace ID. It forwards the user's own token, and ops-api checks the role again per tool. An approval made from an MCP client appears in the incident's resolution under the signing-in user's name, as it does from the console. See ADR 0008.

## 1. One-time: a client registration for interactive sign-in

MCP clients sign users in themselves, so they need a **public client** app registration that may request the Driftline API scope. A tenant admin creates it once:

1. Entra ID → App registrations → **New registration**. Name: `Driftline MCP clients`. Supported accounts: this directory only.
2. **Authentication** → Add a platform → **Mobile and desktop applications** → add `http://localhost` (VS Code and desktop clients use a loopback redirect). Set **Allow public client flows** to **Yes** (device code for scripts).
3. **API permissions** → Add a permission → My APIs → **Driftline API (dl-dev)** → Delegated → `user_impersonation` → **Grant admin consent**.
4. Give each person a role: Enterprise applications → **Driftline API (dl-dev)** → Users and groups → assign **viewer** or **admin**. Without a role, APIM returns 403.

Note the new registration's **Application (client) ID**. It is not a secret.

## 2. VS Code (GitHub Copilot agent mode)

Get a token (valid for about an hour) and keep it out of the file by using an input variable. In `.vscode/mcp.json`:

```json
{
  "inputs": [
    { "id": "driftline-token", "type": "promptString", "description": "Driftline API token", "password": true }
  ],
  "servers": {
    "driftline": {
      "type": "http",
      "url": "https://dl-dev-apim-rf5tbsjvwdzcs.azure-api.net/ops-mcp/mcp",
      "headers": { "Authorization": "Bearer ${input:driftline-token}" }
    }
  }
}
```

To get a token with device code sign-in (uses `azure-identity`, already in the workspace):

```bash
uv run python - <<'PY'
from azure.identity import DeviceCodeCredential
cred = DeviceCodeCredential(client_id="<Driftline MCP clients app id>", tenant_id="<tenant id>")
print(cred.get_token("api://<tenant id>/driftline-api-dl-dev/user_impersonation").token)
PY
```

Start the server from the MCP view and paste the token when asked. Copilot then lists the four tools; `respond_to_approval` asks for confirmation before it runs.

## 3. Claude Desktop

Claude Desktop connects to remote MCP servers as custom connectors. Settings → Connectors → **Add custom connector**, with URL `https://dl-dev-apim-rf5tbsjvwdzcs.azure-api.net/ops-mcp/mcp`. The connector needs a bearer token, and APIM does not yet publish OAuth protected-resource metadata, so Claude Desktop cannot run the Entra sign-in itself.

Until it does, use a local bridge that adds the header. In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "driftline": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote@<pinned version>",
        "https://dl-dev-apim-rf5tbsjvwdzcs.azure-api.net/ops-mcp/mcp",
        "--header", "Authorization:Bearer ${DRIFTLINE_TOKEN}"
      ],
      "env": { "DRIFTLINE_TOKEN": "<token from the device code snippet above>" }
    }
  }
}
```

Pin `mcp-remote` to a reviewed version. Tokens expire after about an hour.

Making sign-in automatic means serving `/.well-known/oauth-protected-resource` from APIM, pointing at the Entra authorization server and the `user_impersonation` scope. That is a follow-up, not part of the definition of done.

## 4. Copilot Studio

Building a Copilot Studio agent is out of scope (spec 5.3). The governance pack in [copilot-studio/](copilot-studio/) covers how an organisation would do it.

In outline, the connection works like this:
- Add an **MCP server** tool in the agent, with the endpoint above.
- Authentication: **OAuth 2.0** with Microsoft Entra ID, using a dedicated confidential app registration owned by the Power Platform team.
- That registration needs the delegated `user_impersonation` permission on Driftline API, and its users need the viewer or admin role.
- Copilot Studio signs each user in, so approvals carry the user's identity.
- The registration's client secret or certificate lives in that team's tenant, not in Driftline.

## Troubleshooting

| Response | Meaning |
|---|---|
| 401 from APIM | token missing, expired, or for another audience |
| 403 with `x-driftline-blocked-by: apim` | the token has neither viewer nor admin |
| tool error "requires one of the roles ['admin']" | a viewer called `respond_to_approval` |
| 409 from `respond_to_approval` | the incident is no longer waiting for a person |
| 429 | per-user rate limit (60 a minute) |
