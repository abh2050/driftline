"""Per-agent managed identities (ADR 0006).

Each agent calls APIM with a token from its own user-assigned identity, so APIM can map the
token's single `agent.*` role to that agent's tool allowlist. The orchestrator's own identity
is used for Foundry, Cosmos, storage and the router.
"""

from __future__ import annotations

import json
import os

from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

from driftline_common.auth import AUDIENCE_ENV


class AgentIdentities:
    def __init__(self) -> None:
        self.client_ids: dict[str, str] = json.loads(os.environ.get("AGENT_IDENTITIES", "{}"))
        self._creds: dict[str, AsyncTokenCredential] = {}

    def credential(self, identity: str) -> AsyncTokenCredential:
        if identity not in self._creds:
            cid = self.client_ids.get(identity)
            # Locally there are no agent identities; fall back to the developer's login,
            # which APIM rejects for tool calls (it carries no agent role).
            self._creds[identity] = (
                ManagedIdentityCredential(client_id=cid) if cid else DefaultAzureCredential()
            )
        return self._creds[identity]

    async def token(self, identity: str) -> str:
        audience = os.environ[AUDIENCE_ENV]
        return (await self.credential(identity).get_token(f"{audience}/.default")).token

    async def close(self) -> None:
        for c in self._creds.values():
            await c.close()
