"""Entra ID auth between Driftline services.

Inbound: validate the bearer JWT (signature via tenant JWKS, issuer, audience, expiry) and
require app roles from the "Driftline API" app registration (ADR 0003).
Outbound: get a managed-identity token for that app registration.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from azure.identity.aio import DefaultAzureCredential
from fastapi import HTTPException, Request, status

AUDIENCE_ENV = "DRIFTLINE_API_AUDIENCE"  # api://<tenant>/driftline-api-<env>
APP_ID_ENV = "DRIFTLINE_API_APP_ID"  # v2 tokens carry the app id as `aud`
TENANT_ENV = "AZURE_TENANT_ID"
AUTH_DISABLED_ENV = "DRIFTLINE_AUTH_DISABLED"  # local development only


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    app_id: str | None
    name: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=4)
def _jwks(tenant: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", cache_keys=True
    )


def verify_token(token: str) -> Principal:
    tenant = os.environ[TENANT_ENV]
    audiences = [a for a in (os.environ.get(APP_ID_ENV), os.environ.get(AUDIENCE_ENV)) if a]
    key = _jwks(tenant).get_signing_key_from_jwt(token)
    claims: dict[str, Any] = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=audiences,
        issuer=[
            f"https://login.microsoftonline.com/{tenant}/v2.0",
            f"https://sts.windows.net/{tenant}/",
        ],
        options={"require": ["exp", "iat", "aud", "iss"]},
    )
    return Principal(
        subject=str(claims.get("oid") or claims.get("sub")),
        roles=frozenset(claims.get("roles", [])),
        app_id=claims.get("azp") or claims.get("appid"),
        name=claims.get("preferred_username") or claims.get("name"),
        claims=claims,
    )


def require_roles(*roles: str) -> Callable[[Request], Awaitable[Principal]]:
    """FastAPI dependency: 401 without a valid token, 403 without one of `roles`."""

    async def dep(request: Request) -> Principal:
        if os.environ.get(AUTH_DISABLED_ENV) == "1":
            return Principal("local", frozenset(roles), None, "local", {})
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        try:
            principal = verify_token(header[7:])
        except (jwt.PyJWTError, KeyError) as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e
        if roles and not (principal.roles & set(roles)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of {sorted(roles)}")
        return principal

    return dep


class ServiceToken:
    """Managed-identity bearer tokens for calling other Driftline services."""

    def __init__(self, credential: DefaultAzureCredential | None = None) -> None:
        self._credential = credential or DefaultAzureCredential()

    async def header(self) -> dict[str, str]:
        audience = os.environ[AUDIENCE_ENV]
        token = await self._credential.get_token(f"{audience}/.default")
        return {"Authorization": f"Bearer {token.token}"}

    async def close(self) -> None:
        await self._credential.close()
