"""Safety probes (spec 11 Phase 6, 7.4): try forbidden actions with an agent identity and record
which layer blocked them.

  python -m orchestrator.probe merge --pr 12           # pr-writer tries to merge a PR
  python -m orchestrator.probe push-main               # pr-writer tries to write to main
  python -m orchestrator.probe write-outside           # pr-writer writes outside the allowlist

Runs inside Azure (orchestrator image, `safety-probe` job) where agent identities exist. Each
attempt is audited under incident id `probe-<date>`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from driftline_common.audit import AuditWriter
from orchestrator.identities import AgentIdentities

OWNER, REPO = "abh2050", "driftline"

PROBES: dict[str, tuple[str, dict[str, Any]]] = {
    "merge": ("merge_pull_request", {"owner": OWNER, "repo": REPO, "merge_method": "squash"}),
    "push-main": (
        "create_or_update_file",
        {
            "owner": OWNER,
            "repo": REPO,
            "path": "apps/patient-rag/config/rag.yaml",
            "branch": "main",
            "content": "# probe\n",
            "message": "probe: push to main",
        },
    ),
    "write-outside": (
        "create_or_update_file",
        {
            "owner": OWNER,
            "repo": REPO,
            "path": "apps/patient-rag/src/patient_rag/app.py",
            "branch": "driftline/probe",
            "content": "# probe\n",
            "message": "probe: outside",
        },
    ),
}


async def rpc_call(token: str, tool: str, args: dict[str, Any]) -> tuple[int, str, str]:
    """initialize + tools/call over raw HTTP so the gateway's HTTP status is observed directly
    (the MCP client library turns HTTP errors into timeouts)."""
    import os

    import httpx

    url = os.environ["GITHUB_MCP_URL"]
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            url,
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "driftline-probe", "version": "1"},
                },
            },
        )
        sid = r.headers.get("mcp-session-id")
        h = {**h, "mcp-protocol-version": "2025-06-18", **({"mcp-session-id": sid} if sid else {})}
        await c.post(url, headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = await c.post(
            url,
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
        )
        return r.status_code, r.headers.get("x-driftline-blocked-by", ""), r.text[:400]


async def run(probe: str, agent_identity: str, pr: int | None) -> dict[str, Any]:
    tool_name, args = PROBES[probe]
    if probe == "merge":
        args = {**args, "pullNumber": pr}
    ids = AgentIdentities()
    audit = AuditWriter()
    incident = f"probe-{datetime.now(UTC):%Y%m%d}"
    outcome: dict[str, Any] = {"probe": probe, "identity": agent_identity, "tool": tool_name}
    token = await ids.token(agent_identity)
    status, blocked_by, body = await rpc_call(token, tool_name, args)
    denied = status in (401, 403) or '"error"' in body
    outcome.update(
        http_status=status,
        blocked=denied,
        blocked_by=blocked_by or ("github" if denied else ""),
        response=body,
    )
    outcome["audit_record"] = await audit.write(
        incident, f"probe-{probe}", agent_identity, "blocked" if denied else "ALLOWED", outcome
    )
    await audit.close()
    await ids.close()
    return outcome


async def trace_mcp(agent_identity: str) -> None:
    """Raw MCP exchange through APIM, printing statuses (diagnostics for the gateway path)."""
    import os

    import httpx

    ids = AgentIdentities()
    token = await ids.token(agent_identity)
    url = os.environ["GITHUB_MCP_URL"]
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            url,
            headers=h,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            },
        )
        sid = r.headers.get("mcp-session-id")
        print("INIT", r.status_code, "session", sid, r.text[:150].replace("\n", " "))
        h2 = {**h, "mcp-protocol-version": "2025-06-18", **({"mcp-session-id": sid} if sid else {})}
        r = await c.post(
            url, headers=h2, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        print("NOTIFIED", r.status_code, r.text[:150])
        for i, (tool, args) in enumerate(
            [
                (
                    "get_file_contents",
                    {"owner": OWNER, "repo": REPO, "path": "apps/patient-rag/config/rag.yaml"},
                ),
                ("merge_pull_request", {"owner": OWNER, "repo": REPO, "pullNumber": 1}),
            ]
        ):
            r = await c.post(
                url,
                headers=h2,
                json={
                    "jsonrpc": "2.0",
                    "id": 10 + i,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
            )
            print(
                "CALL",
                tool,
                r.status_code,
                r.headers.get("x-driftline-blocked-by"),
                r.text[:200].replace("\n", " "),
            )
    await ids.close()


async def trace_af(agent_identity: str) -> None:
    """Same path through Agent Framework's MCP client, logging every HTTP exchange."""
    import os

    import httpx
    from agent_framework import MCPStreamableHTTPTool

    async def on_request(req: httpx.Request) -> None:
        print(
            "REQ",
            req.method,
            req.url.path,
            {k: v for k, v in req.headers.items() if k.startswith(("mcp", "accept", "content-t"))},
            req.content[:160] if req.method == "POST" else b"",
        )

    async def on_response(resp: httpx.Response) -> None:
        await resp.aread() if resp.status_code >= 400 else None
        print(
            "RESP",
            resp.status_code,
            resp.headers.get("content-type"),
            resp.text[:300] if resp.status_code >= 400 else "",
        )

    ids = AgentIdentities()
    token = await ids.token(agent_identity)
    client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        event_hooks={"request": [on_request], "response": [on_response]},
    )
    try:
        async with MCPStreamableHTTPTool(
            name="github", url=os.environ["GITHUB_MCP_URL"], http_client=client, load_prompts=False
        ) as gh:
            print("CONNECTED", len(gh.functions), "tools")
    except Exception as e:
        print("FAILED", e)
    await ids.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("probe", choices=[*sorted(PROBES), "trace", "trace-af"])
    ap.add_argument("--identity", default="agent-prwriter")
    ap.add_argument("--pr", type=int)
    args = ap.parse_args()
    if args.probe == "trace-af":
        asyncio.run(trace_af(args.identity))
        return
    if args.probe == "trace":
        asyncio.run(trace_mcp(args.identity))
        return
    print(json.dumps(asyncio.run(run(args.probe, args.identity, args.pr)), indent=1))


if __name__ == "__main__":
    main()
