"""Create the "Driftline API" Entra app registration and grant app roles (ADR 0003).

Runs as azd hooks:
  preprovision:  uv run python -m scripts.entra_app pre   (app, roles, service principal)
  postprovision: uv run python -m scripts.entra_app post  (role grants to service identities)

Idempotent: reruns update roles in place and only add missing grants. Uses the az CLI's
Microsoft Graph access, so the person running azd needs rights to create app registrations.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from typing import Any

# value -> (description, allowed member types)
ROLES: dict[str, tuple[str, list[str]]] = {
    "viewer": ("Read incidents, reports and chat in the Driftline console", ["User"]),
    "admin": ("Demo controls and approvals in the Driftline console", ["User"]),
    "router.invoke": ("Call the router service", ["Application"]),
    "patientrag.admin": ("Apply and reset faults on patient-rag", ["Application"]),
    "patientrag.query": ("Query patient-rag", ["Application"]),
    "ops.write": ("Resume workflows through ops-api", ["Application"]),
    "agent.retrieval": ("Tool allowlist for retrieval-specialist", ["Application"]),
    "agent.prompt": ("Tool allowlist for prompt-specialist", ["Application"]),
    "agent.index": ("Tool allowlist for index-specialist", ["Application"]),
    "agent.performance": ("Tool allowlist for performance-specialist", ["Application"]),
    "agent.validator": ("Tool allowlist for validator", ["Application"]),
    "agent.prwriter": ("Tool allowlist for pr-writer", ["Application"]),
    "agent.explainer": ("Read-only tool allowlist for explainer", ["Application"]),
    "mcp.gateway": ("APIM calling the MCP backends (github-mcp, kql-tool)", ["Application"]),
}

# service identity -> app roles (spec section 10)
GRANTS: dict[str, list[str]] = {
    "traffic-gen": ["patientrag.query"],
    "detector": ["router.invoke"],
    # patientrag.admin: approved runbooks (re-enable the indexer, reindex)
    "orchestrator": ["router.invoke", "patientrag.admin"],
    "sandbox-runner": ["router.invoke", "patientrag.query"],
    "ops-api": ["router.invoke", "patientrag.admin", "ops.write"],
    # one identity per agent (ADR 0006); APIM maps each role to a tool allowlist
    "agent-retrieval": ["agent.retrieval"],
    "agent-prompt": ["agent.prompt"],
    "agent-index": ["agent.index"],
    "agent-performance": ["agent.performance"],
    "agent-validator": ["agent.validator"],
    "agent-prwriter": ["agent.prwriter"],
    "agent-explainer": ["agent.explainer"],
}

GRAPH = "https://graph.microsoft.com/v1.0"


def az(*args: str) -> Any:
    out = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"az {' '.join(args[:3])} failed: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def graph(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    args = ["rest", "--method", method, "--url", f"{GRAPH}{path}"]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    return az(*args)


def azd_get(key: str) -> str:
    out = subprocess.run(["azd", "env", "get-value", key], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def azd_set(key: str, value: str) -> None:
    subprocess.run(["azd", "env", "set", key, value], check=True, capture_output=True)


def role_id(env_name: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"driftline/{env_name}/{value}"))


def pre() -> None:
    env_name = azd_get("AZURE_ENV_NAME")
    tenant = az("account", "show")["tenantId"]
    name = f"Driftline API ({env_name})"
    roles = [
        {
            "id": role_id(env_name, v),
            "value": v,
            "displayName": v,
            "description": d,
            "allowedMemberTypes": t,
            "isEnabled": True,
        }
        for v, (d, t) in ROLES.items()
    ]
    found = graph("GET", f"/applications?$filter=displayName eq '{name}'")["value"]
    if found:
        app = found[0]
        graph("PATCH", f"/applications/{app['id']}", {"appRoles": roles})
    else:
        app = graph(
            "POST",
            "/applications",
            {
                "displayName": name,
                "signInAudience": "AzureADMyOrg",
                "appRoles": roles,
                "api": {"requestedAccessTokenVersion": 2},
            },
        )
    uri = f"api://{tenant}/driftline-api-{env_name}"
    graph("PATCH", f"/applications/{app['id']}", {"identifierUris": [uri]})
    # The console signs users in with this same registration (Container Apps authentication)
    # and asks for a token for its own API; pre-authorizing itself avoids a consent prompt.
    scope_id = role_id(env_name, "scope/user_impersonation")
    graph(
        "PATCH",
        f"/applications/{app['id']}",
        {
            "api": {
                "requestedAccessTokenVersion": 2,
                "oauth2PermissionScopes": [
                    {
                        "id": scope_id,
                        "value": "user_impersonation",
                        "type": "User",
                        "isEnabled": True,
                        "adminConsentDisplayName": "Use Driftline as the signed-in user",
                        "adminConsentDescription": "The Driftline console calls ops-api for you.",
                        "userConsentDisplayName": "Use Driftline as you",
                        "userConsentDescription": "The Driftline console calls ops-api for you.",
                    }
                ],
            },
            "web": {"implicitGrantSettings": {"enableIdTokenIssuance": True}},
        },
    )
    graph(
        "PATCH",
        f"/applications/{app['id']}",
        {
            "api": {
                "preAuthorizedApplications": [
                    {"appId": app["appId"], "delegatedPermissionIds": [scope_id]}
                ]
            }
        },
    )
    azd_set("DRIFTLINE_API_OBJECT_ID", app["id"])
    sps = graph("GET", f"/servicePrincipals?$filter=appId eq '{app['appId']}'")["value"]
    sp = sps[0] if sps else graph("POST", "/servicePrincipals", {"appId": app["appId"]})
    graph("PATCH", f"/servicePrincipals/{sp['id']}", {"appRoleAssignmentRequired": True})
    azd_set("DRIFTLINE_API_APP_ID", app["appId"])
    azd_set("DRIFTLINE_API_SP_ID", sp["id"])
    azd_set("DRIFTLINE_API_AUDIENCE", uri)
    print(f"entra app: {name} appId={app['appId']} audience={uri}")


def post() -> None:
    env_name = azd_get("AZURE_ENV_NAME")
    sp_id = azd_get("DRIFTLINE_API_SP_ID")
    identities = {i["service"]: i for i in json.loads(azd_get("SERVICE_IDENTITIES") or "[]")}
    existing = graph("GET", f"/servicePrincipals/{sp_id}/appRoleAssignedTo?$top=999")["value"]
    have = {(a["principalId"], a["appRoleId"]) for a in existing}
    added = 0
    for service, roles in GRANTS.items():
        if service not in identities:
            continue
        principal = identities[service]["principalId"]
        for r in roles:
            rid = role_id(env_name, r)
            if (principal, rid) in have:
                continue
            graph(
                "POST",
                f"/servicePrincipals/{sp_id}/appRoleAssignedTo",
                {"principalId": principal, "resourceId": sp_id, "appRoleId": rid},
            )
            added += 1
    apim = azd_get("APIM_PRINCIPAL_ID")
    if apim and (apim, role_id(env_name, "mcp.gateway")) not in have:
        graph(
            "POST",
            f"/servicePrincipals/{sp_id}/appRoleAssignedTo",
            {
                "principalId": apim,
                "resourceId": sp_id,
                "appRoleId": role_id(env_name, "mcp.gateway"),
            },
        )
        added += 1
    # revoke grants that are no longer declared (e.g. agent roles moved off the orchestrator)
    wanted = {
        (identities[s]["principalId"], role_id(env_name, r))
        for s, rs in GRANTS.items()
        for r in rs
        if s in identities
    }
    managed = {i["principalId"] for i in identities.values()}
    for a in existing:
        if a["principalId"] in managed and (a["principalId"], a["appRoleId"]) not in wanted:
            graph("DELETE", f"/servicePrincipals/{sp_id}/appRoleAssignedTo/{a['id']}")
            added -= 1
    console_sign_in(env_name, identities)
    me = azd_get("AZURE_PRINCIPAL_ID")
    if me and (me, role_id(env_name, "admin")) not in have:
        graph(
            "POST",
            f"/servicePrincipals/{sp_id}/appRoleAssignedTo",
            {"principalId": me, "resourceId": sp_id, "appRoleId": role_id(env_name, "admin")},
        )
        added += 1
    print(f"entra app: {added} role grants added, {len(have)} already present")


def console_sign_in(env_name: str, identities: dict[str, Any]) -> None:
    """Redirect URI for the console, and its managed identity as a federated credential so
    Container Apps authentication needs no client secret (ADR 0008)."""
    obj = azd_get("DRIFTLINE_API_OBJECT_ID")
    url = azd_get("CONSOLE_URL")
    if not obj or not url or "console" not in identities:
        return
    app = graph("GET", f"/applications/{obj}?$select=web")
    redirect = f"{url}/.auth/login/aad/callback"
    uris = app["web"].get("redirectUris") or []
    if redirect not in uris:
        graph("PATCH", f"/applications/{obj}", {"web": {"redirectUris": [*uris, redirect]}})
    tenant = az("account", "show")["tenantId"]
    fic = {
        "name": f"console-{env_name}",
        "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "subject": identities["console"]["principalId"],
        "audiences": ["api://AzureADTokenExchange"],
        "description": "Driftline console (Container Apps authentication) managed identity",
    }
    existing = graph("GET", f"/applications/{obj}/federatedIdentityCredentials")["value"]
    if not any(f["name"] == fic["name"] and f["subject"] == fic["subject"] for f in existing):
        for f in existing:
            if f["name"] == fic["name"]:
                graph("DELETE", f"/applications/{obj}/federatedIdentityCredentials/{f['id']}")
        graph("POST", f"/applications/{obj}/federatedIdentityCredentials", fic)
    print(f"entra app: console sign-in at {url}")


if __name__ == "__main__":
    {"pre": pre, "post": post}[sys.argv[1]]()
