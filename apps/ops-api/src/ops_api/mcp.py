"""ops-api's MCP surface (spec 5.1.11, 5.3): the console's read and approval operations as
MCP tools, published through APIM. Callers present their own Driftline API token: viewer to
read, admin to answer an approval, exactly as on the REST routes.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from typing import Any

import jwt
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from driftline_common import auth
from driftline_common.auth import AUTH_DISABLED_ENV, Principal
from ops_api import views
from ops_api.models import Answer

caller: ContextVar[Principal | None] = ContextVar("caller", default=None)
READ_ROLES = {"viewer", "admin"}


def _require(roles: set[str]) -> Principal:
    p = caller.get()
    if p is None or not roles & set(p.roles):
        raise PermissionError(f"requires one of the roles {sorted(roles)}")
    return p


class UserToken:
    """ASGI guard: a valid Driftline API token is required; the principal is kept for tools."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        if os.environ.get(AUTH_DISABLED_ENV) == "1":
            principal: Principal | None = Principal(
                "local", frozenset({"admin"}), None, "local", {}
            )
        else:
            header = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
            try:
                principal = (
                    auth.verify_token(header[7:]) if header.lower().startswith("bearer ") else None
                )
            except (jwt.PyJWTError, KeyError):
                principal = None
        if principal is None or not READ_ROLES & set(principal.roles):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401 if principal is None else 403,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"error":"forbidden"}'})
            return
        # APIM may forward <backend>/mcp to /mcp/mcp; treat it as the same endpoint
        if scope.get("path", "").startswith("/mcp/mcp"):
            scope = {**scope, "path": scope["path"][4:], "raw_path": scope["path"][4:].encode()}
        token = caller.set(principal)
        try:
            await self.inner(scope, receive, send)
        finally:
            caller.reset(token)


def mount_mcp(
    app: FastAPI,
    respond_to: Callable[[str, Any, Principal], Awaitable[dict[str, Any]]],
    list_docs: Callable[..., Awaitable[list[dict[str, Any]]]],
    read_incident: Callable[[str], Awaitable[dict[str, Any]]],
) -> None:
    server = FastMCP(
        "driftline-ops",
        instructions="Driftline incidents and approvals. Read with list_incidents, "
        "get_incident and list_approvals; admins answer with respond_to_approval.",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def list_incidents(limit: int = 20) -> list[dict[str, Any]]:
        """Most recent incidents with status, severity, specialist, action and PR link."""
        _require(READ_ROLES)
        return [views.summary(d) for d in await list_docs(limit=min(max(limit, 1), 100))]

    async def get_incident(incident_id: str) -> dict[str, Any]:
        """Full incident: routing scores, root cause analysis, evaluation, decisions, cost."""
        _require(READ_ROLES)
        return views.detail(await read_incident(incident_id))

    async def list_approvals() -> list[dict[str, Any]]:
        """Runbooks and escalations waiting for a person."""
        _require(READ_ROLES)
        docs = await list_docs(waiting=True, limit=200)
        return [a for d in docs if (a := views.approval(d))]

    async def respond_to_approval(
        incident_id: str, approved: bool, note: str = ""
    ) -> dict[str, Any]:
        """Approve or reject a waiting runbook or escalation (admin role)."""
        p = _require({"admin"})
        return await respond_to(incident_id, Answer(approved=approved, note=note), p)

    for tool in (list_incidents, get_incident, list_approvals, respond_to_approval):
        server.add_tool(tool)

    def lifespan() -> AbstractAsyncContextManager[None]:
        return server.session_manager.run()

    app.state.mcp_lifespan = lifespan
    # mounted last at the root, so REST routes match first and /mcp reaches the MCP server
    app.mount("/", UserToken(server.streamable_http_app()))
