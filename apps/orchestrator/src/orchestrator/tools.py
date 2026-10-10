"""MCP tools through APIM (spec 5.1.12). Agents never reach GitHub or Log Analytics directly."""

from __future__ import annotations

import os
from typing import Any

import mcp.types
from agent_framework import MCPStreamableHTTPTool

from driftline_common.telemetry import inject_context

# APIM's MCP gateway rejects protocol version 2025-11-25 (the mcp SDK's default) with a 500;
# 2025-06-18 is supported end to end (ADR 0006). The session reads this at initialize time.
APIM_MCP_PROTOCOL_VERSION = "2025-06-18"
mcp.types.LATEST_PROTOCOL_VERSION = APIM_MCP_PROTOCOL_VERSION

GITHUB_READ = [
    "get_file_contents",
    "list_commits",
    "get_commit",
    "list_branches",
    "list_pull_requests",
    "pull_request_read",
]


def mcp_tool(kind: str, token: str) -> MCPStreamableHTTPTool:
    headers = {"Authorization": f"Bearer {token}", **inject_context()}
    if kind == "kql":
        return MCPStreamableHTTPTool(
            name="kql",
            url=os.environ["KQL_MCP_URL"],
            static_headers=headers,
            load_prompts=False,
            request_timeout=60,
        )
    allowed: list[str] | None = GITHUB_READ if kind == "github_read" else None
    return MCPStreamableHTTPTool(
        name="github",
        url=os.environ["GITHUB_MCP_URL"],
        static_headers=headers,
        allowed_tools=allowed,
        load_prompts=False,
        request_timeout=60,
    )


def tool_text(result: Any) -> str:
    """Flatten an MCP call result into text."""
    content = getattr(result, "content", None) or []
    return "\n".join(getattr(c, "text", "") for c in content) or str(result)
