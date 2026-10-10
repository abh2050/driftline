from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from ops_api import mcp, views
from ops_api.app import app

import driftline_common.auth as auth
from driftline_common.auth import Principal

DOC: dict[str, Any] = {
    "id": "inc-1",
    "_ts": 100,
    "status": "awaiting_human",
    "severity": "sev1",
    "stream": "replay-x",
    "breached_metrics": ["canary_accuracy"],
    "detected_at": "2026-10-10T20:00:00Z",
    "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
    "pending": {"kind": "runbook", "summary": "indexer stopped", "runbook": ["re-enable"]},
    "workflow": {
        "specialist": "index",
        "specialists": ["index"],
        "action": "runbook",
        "decisions": {"D3": {"chosen": "index", "scores": {"index": 0.85, "retrieval": 0.1}}},
        "costs": {"tokens_usd": 0.01, "decisions_usd": 0.002},
    },
}


def test_summary_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPINSIGHTS_RESOURCE_ID", "/subscriptions/s/appi")
    s = views.summary(DOC)
    assert s["specialist"] == "index"
    assert s["awaiting"] == "runbook"
    assert s["updated"] == 100
    d = views.detail(DOC)
    assert "workflow" not in d
    assert "_ts" not in d
    assert d["decisions"]["D3"]["margin"] == 0.75
    assert "0123456789abcdef0123456789abcdef" in (d["trace_url"] or "")
    assert views.approval(DOC)["kind"] == "runbook"  # type: ignore[index]
    assert views.approval({**DOC, "status": "pr_open"}) is None


@pytest.fixture
def as_role(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv("DRIFTLINE_AUTH_DISABLED", raising=False)

    def use(*roles: str) -> dict[str, str]:
        monkeypatch.setattr(
            auth,
            "verify_token",
            lambda token: Principal("u1", frozenset(roles), None, "user@example.com", {}),
        )
        return {"Authorization": "Bearer t"}

    return use


ADMIN_ROUTES = [
    ("post", "/demo/fault", {"fault_id": "F3"}),
    ("post", "/demo/reset", None),
    ("get", "/demo/state", None),
    ("post", "/demo/replay", {"fault_id": "F3"}),
    ("post", "/incidents/inc-1/respond", {"approved": True}),
    ("post", "/incidents/inc-1/rerun", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_viewer_cannot_use_admin_routes(as_role: Any, method: str, path: str, body: Any) -> None:
    client = TestClient(app)  # no lifespan: requests stop at the role check
    headers = as_role("viewer")
    resp = client.request(method.upper(), path, json=body, headers=headers)
    assert resp.status_code == 403


def test_no_token_is_401() -> None:
    client = TestClient(app)
    assert client.get("/incidents").status_code == 401
    assert client.post("/demo/reset").status_code == 401


def test_me_reports_roles(as_role: Any) -> None:
    client = TestClient(app)
    body = client.get("/me", headers=as_role("viewer")).json()
    assert body == {"name": "user@example.com", "roles": ["viewer"], "admin": False}


def test_demo_rejects_unknown_fault(as_role: Any) -> None:
    client = TestClient(app)
    resp = client.post("/demo/fault", json={"fault_id": "F9"}, headers=as_role("admin"))
    assert resp.status_code == 400


def test_mcp_respond_needs_admin() -> None:
    token = mcp.caller.set(Principal("u1", frozenset({"viewer"}), None, "v", {}))
    try:
        with pytest.raises(PermissionError):
            mcp._require({"admin"})
        assert mcp._require(mcp.READ_ROLES).subject == "u1"
    finally:
        mcp.caller.reset(token)


async def test_mcp_requires_a_token_and_lists_tools(as_role: Any) -> None:
    import httpx

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    accept = {"Accept": "application/json, text/event-stream"}
    transport = httpx.ASGITransport(app=app)
    async with (
        app.state.mcp_lifespan(),
        httpx.AsyncClient(transport=transport, base_url="http://ops") as client,
    ):
        assert (await client.post("/mcp", json=body, headers=accept)).status_code == 401
        resp = await client.post("/mcp", json=body, headers={**accept, **as_role("viewer")})
        assert resp.status_code == 200, resp.text
        doubled = await client.post("/mcp/mcp", json=body, headers={**accept, **as_role("admin")})
        assert doubled.status_code == 200
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {"list_incidents", "get_incident", "list_approvals", "respond_to_approval"}
