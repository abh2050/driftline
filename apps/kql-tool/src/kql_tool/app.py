"""kql-tool (spec 5.1.10): read-only, named KQL queries as MCP tools.

Fronted by APIM (spec 5.1.12); only APIM's managed identity (app role `mcp.gateway`) may call
it. The service identity holds Log Analytics Reader only.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import jwt
from azure.identity.aio import DefaultAzureCredential
from azure.monitor.query import LogsQueryStatus
from azure.monitor.query.aio import LogsQueryClient
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from driftline_common.auth import AUTH_DISABLED_ENV, verify_token
from kql_tool.queries import QueryError, load_all

QUERIES = load_all()
server = FastMCP(
    "driftline-kql",
    instructions="Read-only, named Log Analytics queries about patient-rag and Driftline. "
    "Call list_queries first.",
    host="0.0.0.0",  # noqa: S104 - container listens on all interfaces behind ingress
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
_credential = DefaultAzureCredential()
_client = LogsQueryClient(_credential)


async def list_queries() -> list[dict[str, Any]]:
    """List the named queries and their parameters."""
    return [
        {
            "name": q.name,
            "description": q.description,
            "params": [
                {
                    "name": p.name,
                    "type": p.kind,
                    "default": p.default,
                    **({"range": [p.lo, p.hi]} if p.kind == "int" else {}),
                    **({"choices": list(p.choices)} if p.kind == "enum" else {}),
                }
                for p in q.params
            ],
        }
        for q in QUERIES.values()
    ]


async def run_query(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one named query with validated parameters; returns columns and up to 200 rows."""
    query = QUERIES.get(name)
    if query is None:
        return {"error": f"unknown query {name!r}", "available": sorted(QUERIES)}
    try:
        kql = query.render(params or {})
    except QueryError as e:
        return {"error": str(e)}
    res = await _client.query_workspace(
        os.environ["LOG_ANALYTICS_WORKSPACE_ID"], kql, timespan=timedelta(days=7)
    )
    if res.status != LogsQueryStatus.SUCCESS or not res.tables:
        return {"error": "query failed or returned no table"}
    table = res.tables[0]
    rows = [[str(v) if v is not None else None for v in row] for row in table.rows[:200]]
    return {"query": name, "columns": list(table.columns), "rows": rows}


server.add_tool(list_queries)
server.add_tool(run_query)

_mcp_app = server.streamable_http_app()


class GatewayOnly:
    """ASGI guard: requests must carry a valid token with the `mcp.gateway` role."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("path") != "/healthz"
            and os.environ.get(AUTH_DISABLED_ENV) != "1"
        ):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            status = 401
            if auth.lower().startswith("bearer "):
                try:
                    principal = verify_token(auth[7:])
                    status = 0 if "mcp.gateway" in principal.roles else 403
                except (jwt.PyJWTError, KeyError):
                    status = 401
            if status:
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"forbidden"}'})
                return
        if scope["type"] == "http" and scope.get("path") == "/healthz":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        # APIM forwards to <backend url>/mcp; accept a doubled prefix as the same endpoint.
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp/mcp"):
            scope = {**scope, "path": scope["path"][4:], "raw_path": scope["path"][4:].encode()}
        await self.inner(scope, receive, send)


app = GatewayOnly(_mcp_app)
