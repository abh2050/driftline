"""Driftline agents: Agent Framework agents on Foundry, registered in Foundry Agent Service.

Each agent's definition (model, instructions) is registered as a versioned prompt agent in the
Foundry project at startup, so it is visible, versioned and traced in Foundry. Runs execute
through Agent Framework with local MCP tools, so every tool call carries the agent's own
identity through APIM (ADR 0006).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient, to_prompt_agent
from azure.ai.projects.aio import AIProjectClient
from azure.core.credentials_async import AsyncTokenCredential

from driftline_common.config import AgentsConfig, load_agents_config

log = logging.getLogger("orchestrator.agents")
HOME = Path(os.environ.get("ORCHESTRATOR_HOME", Path(__file__).resolve().parents[2]))
SPECIALISTS = {
    "retrieval": "retrieval-specialist",
    "prompt": "prompt-specialist",
    "index": "index-specialist",
    "performance": "performance-specialist",
}


def instructions(name: str) -> str:
    text = (HOME / "prompts" / f"{name}.md").read_text()
    if name.endswith("-specialist"):
        text = text + "\n\n" + (HOME / "prompts" / "_specialist_common.md").read_text()
    return text


class AgentFactory:
    def __init__(self, credential: AsyncTokenCredential) -> None:
        self.cfg: AgentsConfig = load_agents_config()
        self.credential = credential
        self.project_endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]

    def build(self, name: str, tools: list[Any] | None = None) -> Agent[Any]:
        client = FoundryChatClient(
            project_endpoint=self.project_endpoint,
            model=self.cfg.deployment_for(name),
            credential=self.credential,
        )
        return Agent(client=client, name=name, instructions=instructions(name), tools=tools or None)

    def identity(self, name: str) -> str:
        return self.cfg.agents[name].identity

    async def register_all(self) -> dict[str, str]:
        """Create a new Foundry agent version only when the definition changed."""
        out: dict[str, str] = {}
        async with AIProjectClient(
            endpoint=self.project_endpoint, credential=self.credential
        ) as project:
            for name in self.cfg.agents:
                definition = to_prompt_agent(self.build(name))
                sha = hashlib.sha256(str(definition).encode()).hexdigest()[:16]
                try:
                    latest = await project.agents.get(agent_name=name)
                    meta = (latest.versions.latest.metadata or {}) if latest else {}
                    if meta.get("definition_sha") == sha:
                        out[name] = "unchanged"
                        continue
                except Exception as e:
                    log.debug("agent %s not found: %s", name, e)
                v = await project.agents.create_version(
                    agent_name=name,
                    definition=definition,
                    description=f"Driftline {name}",
                    metadata={"definition_sha": sha, "source": "driftline"},
                )
                out[name] = f"version {v.version}"
        log.info("foundry agents: %s", out)
        return out
