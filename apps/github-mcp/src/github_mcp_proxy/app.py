"""github-mcp token proxy (spec 5.1.13, ADR 0006).

GitHub's MCP server in HTTP mode expects every request to carry a GitHub token; GitHub App
auth exists only in its stdio mode. This proxy sits in front of it in the same container
app:

- inbound: accepts only callers holding the `mcp.gateway` app role (APIM's managed identity),
  so agents cannot bypass APIM's policies;
- mints a GitHub App installation token from the private key in Key Vault, restricted to the
  repository and permissions in config/github.yaml, and caches it until shortly before expiry;
- streams the request to the MCP server on localhost with that token.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any

import httpx
import jwt
import yaml
from azure.identity.aio import DefaultAzureCredential
from azure.keyvault.secrets.aio import SecretClient
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from driftline_common.auth import Principal, require_roles
from driftline_common.config import config_dir

UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://127.0.0.1:8082")
HOP_HEADERS = {"host", "authorization", "content-length", "connection", "transfer-encoding"}
Gateway = Annotated[Principal, Depends(require_roles("mcp.gateway"))]


class InstallationTokens:
    def __init__(self, cfg: dict[str, Any], key_vault_uri: str) -> None:
        self.cfg = cfg
        self._kv = SecretClient(key_vault_uri, DefaultAzureCredential())
        self._key: str | None = None
        self._token = ""
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def get(self, http: httpx.AsyncClient) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires - 300:
                return self._token
            if self._key is None:
                self._key = (await self._kv.get_secret("github-app-private-key")).value
            if not self._key:
                raise RuntimeError("github-app-private-key is empty")
            now = int(time.time())
            app_jwt = jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": str(self.cfg["app_id"])},
                self._key,
                algorithm="RS256",
            )
            resp = await http.post(
                f"https://api.github.com/app/installations/{self.cfg['installation_id']}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                },
                json={
                    "repositories": [self.cfg["repository"].split("/")[1]],
                    "permissions": self.cfg["token_permissions"],
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token = body["token"]
            self._expires = datetime.fromisoformat(body["expires_at"]).timestamp()
            return self._token

    async def close(self) -> None:
        await self._kv.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = yaml.safe_load((config_dir() / "github.yaml").read_text())
    app.state.tokens = InstallationTokens(cfg, os.environ["KEYVAULT_URI"])
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.tokens.close()


app = FastAPI(title="github-mcp-proxy", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(path: str, request: Request, _: Gateway) -> StreamingResponse:
    http: httpx.AsyncClient = app.state.http
    token = await app.state.tokens.get(http)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["Authorization"] = f"Bearer {token}"
    upstream = http.build_request(
        request.method,
        f"{UPSTREAM}/{path}",
        params=request.query_params,
        headers=headers,
        content=await request.body(),
    )
    resp = await http.send(upstream, stream=True)
    out_headers = {
        k: v for k, v in resp.headers.items() if k.lower() not in HOP_HEADERS | {"content-encoding"}
    }
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=out_headers,
        background=BackgroundTask(resp.aclose),
    )
